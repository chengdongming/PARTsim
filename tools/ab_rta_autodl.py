#!/usr/bin/env python3
"""Install and run the pinned AB RTA F2 experiment on a fresh AutoDL server.

Python >=3.11, Linux. No Python packages are needed to run this installer.
Run: python ab_rta_autodl.py start
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager, ExitStack
from datetime import datetime, timezone, timedelta
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import time
import uuid
import zipfile

VERSION = 1
REPOSITORY = "https://github.com/chengdongming/PARTsim.git"
CORE_COMMIT = "1563ec7d63f46a059c41314d8ca1449ff0933e3f"
CORE_SOURCE = "f0fb5bdc6f9f847d8fa70eeb39be77a6ec4cadbebdfee2850ba52dc1621c1014"
PACKAGES = {"PyYAML": "6.0.3", "numpy": "2.3.5", "matplotlib": "3.10.8"}
SUITES = ["main", "mechanism", "scale", "robustness", "timing", "numeric_scale", "mechanism_timing"]


def now():
    return datetime.now(timezone.utc).isoformat()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


@contextmanager
def lock(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError(f"另一个程序正在使用 {path}；请勿重复启动。") from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def session_name(root):
    return "abf2_" + hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:12]


def active(root):
    if not shutil.which("tmux"):
        return False
    return subprocess.run(["tmux", "has-session", "-t", "=" + session_name(root)],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


def command(argv, root, *, capture=False, timeout=600, cwd=None):
    """No shell evaluation; detailed setup output stays in a log."""
    with (root / "logs/setup.log").open("a", encoding="utf-8") as log:
        log.write("\n" + now() + " " + shlex.join(map(str, argv)) + "\n")
        log.flush()
        result = subprocess.run(list(map(str, argv)), cwd=cwd, text=True,
                                stdout=subprocess.PIPE if capture else log,
                                stderr=log, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"命令失败（退出码{result.returncode}），请查看 {root / 'logs/setup.log'}")
    return result.stdout.strip() if capture else ""


def system_tools(root):
    missing = [name for name in ("git", "tmux") if not shutil.which(name)]
    if not missing:
        return
    if os.geteuid() != 0 or not shutil.which("apt-get"):
        raise RuntimeError("缺少 " + ", ".join(missing) + "；请先安装git和tmux。")
    print("安装系统工具：" + ", ".join(missing), flush=True)
    command(["apt-get", "update"], root)
    command(["apt-get", "install", "-y", "--no-install-recommends", *missing, "ca-certificates"], root)


def checkout(root):
    repo = root / "PARTsim"
    if repo.exists() and not (repo / ".git").exists() and any(repo.iterdir()):
        raise RuntimeError(f"{repo}已有其他文件，请使用新的 --root 目录。")
    if not (repo / ".git").exists():
        command(["git", "init", repo], root)
        command(["git", "-C", repo, "remote", "add", "origin", REPOSITORY], root)
    origin = command(["git", "-C", repo, "remote", "get-url", "origin"], root, capture=True)
    if origin != REPOSITORY:
        raise RuntimeError("已有目录指向其他仓库，拒绝覆盖。")
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", "HEAD"],
                          capture_output=True, text=True)
    if head.returncode == 0:
        if head.stdout.strip() != CORE_COMMIT:
            raise RuntimeError("已有目录不是固定实验版本，请使用新的 --root 目录。")
        if command(["git", "-C", repo, "status", "--porcelain", "--untracked-files=no"], root, capture=True):
            raise RuntimeError("实验代码存在本地修改，拒绝覆盖或混合运行。")
        return repo
    print("拉取固定实验版本 " + CORE_COMMIT[:12] + " …", flush=True)
    for attempt in range(3):
        try:
            command(["git", "-C", repo, "-c", "http.lowSpeedLimit=1024", "-c", "http.lowSpeedTime=60",
                     "fetch", "--depth=1", "--filter=blob:none", "origin", CORE_COMMIT], root, timeout=240)
            break
        except (RuntimeError, subprocess.TimeoutExpired):
            if attempt == 2:
                raise RuntimeError("GitHub拉取失败。请检查服务器网络或AutoDL学术资源加速，修好后重跑同一命令；已有数据保留。") from None
            time.sleep(2)
    command(["git", "-C", repo, "sparse-checkout", "init", "--cone"], root)
    command(["git", "-C", repo, "sparse-checkout", "set", "experiments/asapblock_f2", "experiments/asapblock_unlimited"], root)
    command(["git", "-C", repo, "checkout", "--detach", CORE_COMMIT], root)
    return repo


def options(args, old=None):
    if old:
        for key in ("workers", "timing_workers", "verification_workers"):
            requested = getattr(args, key)
            if requested is not None and requested != old[key]:
                raise RuntimeError("这个目录的并发配置已固定；改变配置请使用新的 --root。")
        if args.serial_timing and old["timing_workers"] != 1:
            raise RuntimeError("这个目录使用并发计时；切换串行计时请使用新的 --root。")
        return {key: old[key] for key in ("workers", "timing_workers", "verification_workers")}
    workers = args.workers or 28
    if args.serial_timing and args.timing_workers not in (None, 1):
        raise RuntimeError("--serial-timing不能与大于1的--timing-workers同时使用。")
    return {"workers": workers, "timing_workers": 1 if args.serial_timing else (args.timing_workers or workers),
            "verification_workers": args.verification_workers or workers}


def verify_environment(root):
    python = root / "venv/bin/python"
    repo = root / "PARTsim"
    code = ("import sys,json,importlib.metadata;sys.path.insert(0,'experiments/asapblock_f2');"
            "import common as C;print(json.dumps({'source':C.source_identity()['sha256'],"
            "'packages':{n:importlib.metadata.version(n) for n in ['PyYAML','numpy','matplotlib']}}))")
    value = json.loads(command([python, "-c", code], root, capture=True, cwd=repo))
    if value != {"source": CORE_SOURCE, "packages": PACKAGES}:
        raise RuntimeError("源码或Python依赖与已验证版本不一致，停止启动并保留数据。")


def setup(root, args):
    old = read(root / "install.json") if (root / "install.json").exists() else None
    selected = options(args, old)
    if old and (old.get("core_commit") != CORE_COMMIT or old.get("version") != VERSION):
        raise RuntimeError("目录属于其他启动器/代码版本，请使用新的 --root。")
    system_tools(root)
    repo = checkout(root)
    python = root / "venv/bin/python"
    if not old:
        print("创建独立Python环境并安装固定依赖 …", flush=True)
        if not python.exists():
            command([sys.executable, "-m", "venv", root / "venv"], root)
        command([python, "-m", "pip", "install", "--disable-pip-version-check", "--timeout", "30", "--retries", "3",
                 *[name + "==" + version for name, version in PACKAGES.items()]], root, timeout=1200)
    verify_environment(root)
    f2 = repo / "experiments/asapblock_f2"
    resource_args = [python, f2 / "campaign.py", "--resources-only", "--workers", str(selected["workers"]),
                     "--timing-workers", str(selected["timing_workers"]),
                     "--verification-workers", str(selected["verification_workers"])]
    resources = json.loads(command(resource_args, root, capture=True, cwd=repo))
    frozen_plan = root / "results/campaign_plan.json"
    if old and frozen_plan.exists() and resources["environment"] != read(frozen_plan)["environment"]:
        raise RuntimeError("硬件或运行环境与已有正式批次不同；原结果保留，请使用新的 --root 运行新批次。")
    write(root / "resources.json", resources)
    if not old:
        print("运行33项代码自检（一般需要一两分钟）…", flush=True)
        with (root / "logs/selftest.log").open("w", encoding="utf-8") as output:
            result = subprocess.run([str(python), str(f2 / "test_f2.py")], cwd=repo,
                                    stdout=output, stderr=subprocess.STDOUT)
        if result.returncode:
            raise RuntimeError(f"代码自检失败，正式队列未启动；请查看 {root / 'logs/selftest.log'}")
        freeze = command([python, "-m", "pip", "freeze"], root, capture=True)
        (root / "environment_freeze.txt").write_text(freeze + "\n", encoding="utf-8")
        write(root / "install.json", {"version": VERSION, "core_commit": CORE_COMMIT, "source_sha256": CORE_SOURCE,
                                     "packages": PACKAGES, "created_utc": now(), **selected})
    print("实际分配：覆盖率 {0} 路，重复计时 {1} 路，独立核验 {2} 路。".format(
        resources["coverage"]["effective_workers"], resources["timing"]["effective_workers"],
        resources["verification"]["effective_workers"]), flush=True)
    return read(root / "install.json")


@contextmanager
def idle_output(output):
    with ExitStack() as stack:
        stack.enter_context(lock(output / "campaign.lock"))
        for suite in SUITES:
            folder = output / suite
            if folder.exists():
                stack.enter_context(lock(folder / "run.lock"))
        yield


def repair_indexes(output):
    """Recover only a torn final JSONL write, preserving its original bytes."""
    repaired = []
    with idle_output(output):
        for suite in SUITES:
            for kind in ("primary", "verification", "diagnostics"):
                path = output / suite / (kind + ".jsonl")
                if not path.exists():
                    continue
                raw = path.read_bytes()
                if not raw or raw.endswith(b"\n"):
                    continue
                lines = raw.splitlines()
                complete = []
                for index, line in enumerate(lines):
                    try:
                        complete.append(json.loads(line))
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        if index != len(lines) - 1:
                            raise RuntimeError(f"日志中段损坏，需人工核对：{path}") from None
                copies = [read(p) for p in path.with_suffix(".records").glob("*.json")]
                by = {r["request_id"]: r for r in copies}
                if len(by) != len(copies) or len({r["request_id"] for r in complete}) != len(complete):
                    raise RuntimeError(f"日志存在重复身份，需人工核对：{path}")
                if any(r["request_id"] not in by or canonical(by[r["request_id"]]) != canonical(r) for r in complete):
                    raise RuntimeError(f"日志和独立记录不一致，拒绝自动修改：{path}")
                if not copies:
                    raise RuntimeError(f"没有独立记录可恢复，原日志保留：{path}")
                backup = path.with_name(path.name + ".interrupted_" + uuid.uuid4().hex[:10])
                shutil.copy2(path, backup)
                temporary = path.with_name(path.name + ".recovered")
                with temporary.open("w", encoding="utf-8") as stream:
                    for row in sorted(copies, key=lambda r: r["request_id"]):
                        stream.write(canonical(row) + "\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, path)
                repaired.append({"path": str(path), "backup": str(backup), "records": len(copies)})
    return repaired


def campaign_argv(root, config):
    return [str(root / "venv/bin/python"), "-u", str(root / "PARTsim/experiments/asapblock_f2/campaign.py"),
            "--output", str(root / "results"), "--workers", str(config["workers"]),
            "--timing-workers", str(config["timing_workers"]),
            "--verification-workers", str(config["verification_workers"])]


def run_worker(root):
    with lock(root / "worker.lock"):
        config = read(root / "install.json")
        verify_environment(root)
        repaired = repair_indexes(root / "results")
        write(root / "run_state.json", {"state": "RUNNING", "started_utc": now(), "index_repairs": repaired})
        with (root / "logs/console.log").open("a", encoding="utf-8") as log:
            log.write("\n" + now() + " 开始/续跑固定版本F2\n")
            log.flush()
            code = subprocess.run(campaign_argv(root, config), cwd=root / "PARTsim",
                                  stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT).returncode
        state = "COMPLETE" if code == 0 else "FAILED"
        write(root / "run_state.json", {"state": state, "finished_utc": now(), "exit_code": code})
        return code


def worker(root):
    try:
        return run_worker(root)
    except BaseException as error:
        write(root / "run_state.json", {"state": "FAILED", "finished_utc": now(), "error": str(error)})
        with (root / "logs/console.log").open("a", encoding="utf-8") as log:
            log.write(now() + " 启动/恢复失败：" + str(error) + "\n")
        raise


def status(root):
    running = active(root)
    path = root / "results/campaign_status.json"
    progress = read(path) if path.exists() else None
    launch = read(root / "run_state.json") if (root / "run_state.json").exists() else {}
    state = "RUNNING" if running else launch.get("state", "NOT_STARTED")
    if not running and state == "RUNNING":
        state = "INTERRUPTED"
    return {"state": state, "background_session_alive": running, "session": session_name(root),
            "progress": progress, "run_directory": str(root),
            "note": "进度文件可能停留在中断前的RUNNING；实际运行状态以上述后台会话为准。"}


def start(root, args):
    with lock(root / "start.lock"):
        if active(root):
            print("实验已经在后台运行，不会重复启动。")
            print(json.dumps(status(root), ensure_ascii=False, indent=2))
            return
        setup(root, args)
        target = root / "controller.py"
        if Path(__file__).resolve() != target:
            shutil.copy2(Path(__file__).resolve(), target)
        # A quoted argv is passed as tmux's single shell-command argument.
        argv = [sys.executable, str(target), "_worker", "--root", str(root)]
        command(["tmux", "new-session", "-d", "-s", session_name(root), shlex.join(argv)], root)
        print("已启动后台实验。SSH/网页断开后可继续运行；实例关机后需重新执行resume。")
        print("日志：" + str(root / "logs/console.log"))
        print("结果：" + str(root / "results"))
        print("查看进度：python " + shlex.quote(str(target)) + " status --root " + shlex.quote(str(root)))


def pack(root):
    if active(root):
        raise RuntimeError("实验仍在运行。请在结束后打包；运行期间可下载results目录中的数据检查点。")
    if not (root / "install.json").exists():
        raise RuntimeError("这个目录还没有实验配置。")
    stamp = datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d_%H%M%S")
    result = root / ("F2_server_results_" + stamp + ".zip")
    with idle_output(root / "results"), zipfile.ZipFile(result, "w", zipfile.ZIP_DEFLATED, compresslevel=3) as archive:
        for base in (root / "results", root / "logs"):
            for path in sorted(base.rglob("*")):
                if path.is_file() and path.suffix not in (".lock", ".tmp") and path.name != "F2_formal_checkpoint_20261021.zip":
                    archive.write(path, path.relative_to(root))
        for name in ("install.json", "resources.json", "run_state.json", "environment_freeze.txt"):
            if (root / name).exists():
                archive.write(root / name, name)
        archive.writestr("archive_status.json", canonical(status(root)))
    with zipfile.ZipFile(result) as archive:
        if archive.testzip():
            raise RuntimeError("压缩包CRC检查失败。")
    print("压缩包：" + str(result))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", nargs="?", default="start", choices=["start", "resume", "setup", "status", "logs", "pack", "_worker"])
    default_root = (Path("/root/autodl-tmp") if Path("/root/autodl-tmp").is_dir() else Path.cwd()) / "ab_rta_f2"
    parser.add_argument("--root", type=Path, default=default_root)
    parser.add_argument("--workers", type=int)
    parser.add_argument("--timing-workers", type=int)
    parser.add_argument("--verification-workers", type=int)
    parser.add_argument("--serial-timing", action="store_true")
    args = parser.parse_args(argv)
    if sys.version_info < (3, 11) or sys.platform != "linux":
        parser.error("请使用Linux和Python 3.11或以上；你选的Python 3.12镜像满足要求。")
    for key in ("workers", "timing_workers", "verification_workers"):
        if getattr(args, key) is not None and getattr(args, key) < 1:
            parser.error("并发数必须为正整数。")
    root = args.root.expanduser().resolve()
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[name] = "1"
    try:
        if args.action == "status":
            print(json.dumps(status(root), ensure_ascii=False, indent=2))
        elif args.action == "logs":
            path = root / "logs/console.log"
            if not path.exists():
                raise RuntimeError("正式日志尚未创建；首次安装时可查看logs/setup.log和logs/selftest.log。")
            subprocess.run(["tail", "-n", "50", "-f", str(path)])
        elif args.action == "pack":
            pack(root)
        else:
            root.mkdir(parents=True, exist_ok=True)
            (root / "logs").mkdir(exist_ok=True)
            if args.action == "_worker":
                return worker(root)
            if args.action == "resume" and not (root / "install.json").exists():
                raise RuntimeError("尚未完成首次配置，请先执行start。")
            if args.action == "setup":
                with lock(root / "start.lock"):
                    if active(root):
                        raise RuntimeError("实验运行中，不重复配置环境。")
                    setup(root, args)
            else:
                start(root, args)
    except KeyboardInterrupt:
        return 130
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as error:
        print("错误：" + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
