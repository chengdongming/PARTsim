# ASAP-BLOCK 局部窗口固定点响应时间分析与正确性证明（v21 experiment-ready 版）


> **v21 experiment-ready 修订说明。** 本版在 proof-taskset strengthened 版基础上做实现前小修：统一 certified carry-in 记号为 $\Theta_i=R_i^{\Theta,\mathrm{loc},UB}$；在第 8.1 节补充 $A_k^\Theta(w)>w$ 时候选窗口不可能闭合；在第 10.5 节标题与引理前提中明确该局部转换引理处于第 10.6 节的 taskset-level first-counterexample 语境；并补充前缀级阻塞/需求证书与保守能量需求 $E_k^\Theta(\omega^\star)$ 之间的关系，说明证书能量账是消除局部阻塞所需支付的保守上界，而不是声称所有单位均已在窗口内完成。


> **v21 proof-taskset strengthened 修订说明。** 本版在 proof-closure strengthened 版基础上进一步修补主定理边界：第 10.6 节不再把 v21 写成脱离任务集语境的孤立单任务定理，而是恢复为 taskset-level first-counterexample 证明。这样可以明确低优先级并发项中使用 $W_j^D(x+\delta)$ 的安全前提：在全系统第一个违反候选响应时间上界的反例之前，所有任务均未超过其候选上界，且由于 $R_j^{\Theta,\mathrm{loc},UB}\le D_j$，也不存在已到达绝对截止期而未完成的低优先级旧作业。因此，低优先级 backlog 不会任意积压，deadline-parameterized workload bound $W_j^D(\cdot)$ 可以安全覆盖局部窗口中的低优先级并发需求。


> **v21 proof-closure strengthened 修订说明。** 本版在 v21 proof-strengthened 版基础上继续补强闭合证明：局部窗口包含引理明确区分“窗口内造成未完成的阻塞/需求证书”和“窗口之后参与最终完成的未来执行”；第 10.5 节从“真实窗口内已经观察到完整 $\omega^\star$”改为“若前缀未能在 $x+\delta$ 内闭合，则存在一个可由局部窗口约束覆盖的前缀级阻塞/需求证书 $\omega^\star$”，避免把参考前缀理论结构误写成真实窗口中已经全部发生的执行；第 12 节补充跳跃式搜索 $\delta\leftarrow G(\delta)$ 的正确性理由。此前不安全的局部纯算力截断 $[x+\delta-C_k+1]^+$ 已删除，A6/A8 假设已显式列出，v21 与 v20.4 的关系仍保持为带前提的连接关系.

> **v21 定位。** 本版是在 v20.4 的基础上做结构性紧化，而不是推倒重来。v20.4 已经包含 certified carry-in、处理器容量耦合约束和窗口级容量约束；v21 保留这些安全结构，只把能量偏移项中的完整候选窗口工作量 $W_i^\Theta(w)$ 替换为针对每个参考前缀 $x$ 的局部覆盖窗口工作量 $W_i^\Theta(x+\delta)$，并对每个 $x$ 引入内部闭合偏移 $\delta_x^\star$。因此，v21 可以理解为
>
> $$
> \boxed{\text{v21}=\text{v20.4}+\text{local-window inner fixed point}.}
> $$
>
> 本版仍然保留
>
> $$
> U(\omega)=\sum_{\tau_i\in hp(k)}u_i,
> $$
>
> 不直接把 $U$ 除以处理器数，也不假设额外高优先级先行执行必然并行发生。v21 的紧化来自缩小可行变量集合 $\Omega$，而不是改变 $U$ 的时间账解释。

---

# 1. 从 v20.4 到 v21 的核心差异

v20.4 的主函数为

$$
F_k^\Theta(w)=A_k^\Theta(w)+B_k^{E,\Theta}(w),
$$

其中

$$
A_k^\Theta(w)=C_k+D_k^{P,\Theta}(w).
$$

v20.4 的保守性主要来自：对每一个纯算力参考前缀 $x\le A_k^\Theta(w)$，在构造能量需求变量组时都使用完整候选窗口 $w$ 的工作量上界，例如

$$
a_i+b_i+u_i\le \min\{W_i^\Theta(w),w\}.
$$

但是，完成参考前缀 $x$ 实际只需要考虑覆盖该前缀的真实局部时间窗口。如果该前缀需要额外偏移 $\delta$，则相关执行只发生在长度

$$
L(x,\delta)=x+\delta
$$

的局部窗口中。于是 v21 将上述约束局部化为

$$
\boxed{
a_i+b_i+u_i\le \min\{W_i^\Theta(x+\delta),x+\delta\}.
}
$$

这一步直接收紧以下保守链条：

$$
W_i^\Theta(w)\text{ 偏大}
\Rightarrow u_i\text{ 可取偏大}
\Rightarrow U(\omega)=\sum_i u_i\text{ 偏大}
\Rightarrow B_k^{E,\Theta}(w)\text{ 偏大}.
$$

v21 不修改纯算力参考项 $A_k^\Theta(w)$，只替换能量偏移项：

$$
B_k^{E,\Theta}(w)
\quad\Longrightarrow\quad
B_k^{E,\Theta,\mathrm{loc}}(w).
$$

因此 v21 的外层主函数为

$$
\boxed{
F_k^{\Theta,\mathrm{loc}}(w)
=
A_k^\Theta(w)+B_k^{E,\Theta,\mathrm{loc}}(w).
}
$$

---

# 2. 系统模型与沿用假设

v21 沿用 v20.4 的系统模型与调度语义。系统包含 $M$ 个相同处理器核心，所有核心共享一个储能单元。任务集为

$$
\tau=\{\tau_1,\tau_2,\ldots,\tau_n\}.
$$

每个任务为顺序周期任务：

$$
\tau_i=(C_i,T_i,D_i,\hat P_i),
$$

其中 $C_i$ 是最坏执行时间，$T_i$ 是周期或最小释放间隔，$D_i$ 是相对截止期，$\hat P_i$ 是单位时间最坏能量消耗。任务完整执行一次所需最坏能量为

$$
E_i=C_i\hat P_i.
$$

本文继续采用离散时间模型，所有释放、执行、能量检查和完成判断均发生在整数边界上。任务是顺序任务，即同一个任务在一个单位时间内最多占用一个处理器核心。任务集满足受限截止期：

$$
C_i\le D_i\le T_i.
$$

对目标任务 $\tau_k$，定义高优先级任务集合与低优先级任务集合：

$$
hp(k)=\{\tau_i\mid \pi_i>\pi_k\},
\qquad
lp(k)=\{\tau_j\mid \pi_j<\pi_k\}.
$$

能量服务曲线沿用 v20.4。令 $\beta_l(\Delta)$ 表示任意长度为 $\Delta$ 的真实时间区间内系统至少能够在线收集并可用于执行的能量下界。假设 $\beta_l$ 单调不减且 $\beta_l(0)=0$。离散反函数定义为

$$
\beta_{l,\mathbb Z}^{-1}(E)
=
\min\{\Delta\in\mathbb Z_{\ge0}\mid E\le \beta_l(\Delta)\}.
$$

若不存在有限整数 $\Delta$ 满足该条件，则约定

$$
\beta_{l,\mathbb Z}^{-1}(E)=\infty.
$$

$v21$ 同样依赖以下语义边界：$\beta_l$ 必须是在线可用能量服务曲线，而不是仅在区间末端统计的离线累计收集量；若存在储能溢出，则 $\beta_l$ 必须已经扣除溢出损失；$E_0$ 表示任意分析窗口起点，即目标作业释放时刻，可保证存在的储能下界。

为避免后续证明中引用不清，本版显式列出两个与 v20.4 相同的能量语义假设。

**A6（在线能量服务下界）。** 对任意真实时间区间，只要长度为 $\Delta$，系统在该区间内能够按时间顺序在线获得并用于执行的能量至少为 $\beta_l(\Delta)$。因此，$\beta_l$ 不是只在区间末端统计的离线累计收能量；若储能容量导致收集能量溢出，则 $\beta_l$ 已经按最坏情况扣除了该溢出损失。

**A8（单位时间前缀执行可支付性）。** 在每个单位时间边界，ASAP-BLOCK 对最多 $M$ 个作业组成的高优先级连续前缀进行能量检查。若该前缀通过检查，则储能容量、能量扣除顺序和单位时间执行语义足以支持该前缀在该 tick 内执行。换言之，分析中被计入同一单位时间执行集合的能量需求不会因为储能容量或扣除顺序而额外失效；若系统存在更强的容量限制，则需要将其反映到 $\beta_l$ 或调度可执行性假设中。

---

# 3. ASAP-BLOCK 调度语义

ASAP-BLOCK 在每个单位时间起点按照固定优先级从高到低扫描当前活动作业。设当前可用能量为 $E(t)$，已经选入本单位时间执行集合的作业集合为 $S$。若当前扫描到的作业 $J$ 满足仍有空闲核心，并且剩余能量足以支持它执行一个单位时间，即

$$
E(t)-\sum_{J_h\in S}\hat P_{J_h}\ge \hat P_J,
$$

则把 $J$ 加入 $S$。若当前作业因能量不足无法执行，则扫描停止，任何更低优先级作业都不能绕过它执行。

因此，ASAP-BLOCK 在每个单位时间选择的是一个高优先级连续前缀。若最高优先级活动作业本身能量不足，则该单位时间内系统完全空闲，只收集能量。

---

# 4. Certified carry-in 工作量上界

对任意任务 $\tau_i$ 和带入滞留参数 $\theta_i$，其中

$$
C_i\le \theta_i\le D_i\le T_i,
$$

定义

$$
N_i^\theta(L)=
\left\lfloor\frac{L+\theta_i-C_i}{T_i}\right\rfloor,
$$

$$
W_i^\theta(L)=
N_i^\theta(L)C_i+
\min\left(C_i,
L+\theta_i-C_i-N_i^\theta(L)T_i
\right).
$$

当取 $\theta_i=D_i$ 时，记为

$$
W_i^D(L).
$$

当分析目标任务 $\tau_k$ 时，若高优先级任务 $\tau_i\in hp(k)$ 已经按优先级顺序被证明存在响应时间上界

$$
R_i^{\Theta,\mathrm{loc},UB}\le D_i,
$$

则可取

$$
\Theta_i=R_i^{\Theta,\mathrm{loc},UB}
$$

并使用

$$
W_i^\Theta(L)=W_i^{\theta_i=\Theta_i}(L).
$$

因此，本文 v21 版本中的 certified carry-in 参数来自 v21-local 递推认证结果，而不是固定引用 v20.4 的上界。若实现中为了 fallback 使用其他已认证上界，需要在实验报告中单独说明。

由于 $\Theta_i\le D_i$，有

$$
W_i^\Theta(L)\le W_i^D(L).
$$

v21 与 v20.4 一样，只对已经认证的高优先级任务使用 $W_i^\Theta$。低优先级任务仍使用 $W_j^D$，以避免对尚未分析的低优先级任务引入循环依赖。

---

# 5. 纯算力参考进度

v21 保留 v20.4 的纯算力参考进度定义。

对目标任务 $\tau_k$，定义高优先级有效算力干扰：

$$
\bar W_{i,k}^{P,\Theta}(w)
=
\min\left(W_i^\Theta(w),[w-C_k+1]^+\right).
$$

纯算力排队延迟上界为

$$
D_k^{P,\Theta}(w)=
\max\left\{
 d\in\mathbb Z_{\ge0}
 \;\middle|\;
 \sum_{\tau_i\in hp(k)}
 \min\left(\bar W_{i,k}^{P,\Theta}(w),d\right)
 \ge Md
\right\}.
$$

于是纯算力参考进度总长度为

$$
\boxed{
A_k^\Theta(w)=C_k+D_k^{P,\Theta}(w).
}
$$

在参考进度长度 $x$ 下，目标作业已经执行的参考执行量 $z$ 满足

$$
Z_k^\Theta(w,x)=
\left[
\max(0,x-D_k^{P,\Theta}(w)),
\min(C_k,x)
\right],
$$

并且

$$
z\in Z_k^\Theta(w,x)\cap\mathbb Z_{\ge0}.
$$

当 $x=A_k^\Theta(w)$ 时，必有 $z=C_k$，因此完整参考进度完成意味着目标作业完成。

---

# 6. v21 局部窗口变量集合

v21 的关键变化发生在能量偏移分析中。对固定候选窗口 $w$、参考前缀 $x$ 和局部偏移 $\delta$，定义局部覆盖窗口长度

$$
\boxed{
L(x,\delta)=x+\delta.
}
$$

$v20.4$ 在能量变量集合中使用完整窗口 $w$；$v21$ 使用局部窗口 $L(x,\delta)$。

下文只考虑

$$
1\le x\le A_k^\Theta(w),
\qquad
\delta\in\mathbb Z_{\ge0}.
$$

对于 $x=0$，规定所有变量均为 0，局部偏移为 0。

定义局部可行变量集合

$$
\Omega_k^{\Theta,\mathrm{loc}}(w,x,\delta)
$$

为所有非负整数变量组

$$
\omega=(z,a_i,b_i,u_i,c_j)
$$

满足以下约束。

## 6.1 参考执行量约束

$$
z\in Z_k^\Theta(w,x)\cap\mathbb Z_{\ge0}.
$$

其中 $z$ 表示参考前缀 $x$ 中目标作业 $\tau_k$ 已经执行的单位数。

## 6.2 高优先级变量非负性

对所有 $\tau_i\in hp(k)$：

$$
a_i,b_i,u_i\in\mathbb Z_{\ge0}.
$$

其中：

- $a_i$：被纯算力参考等待阶段吸收的高优先级执行量；
- $b_i$：目标执行阶段中与目标并发的高优先级执行量；
- $u_i$：没有被参考进度 $x$ 计入的高优先级额外先行执行量。

## 6.3 高优先级局部工作量约束

v21 将 v20.4 的完整窗口约束局部化为

$$
\boxed{
a_i+b_i+u_i
\le
\min\{W_i^\Theta(L(x,\delta)),L(x,\delta)\},
\qquad \forall \tau_i\in hp(k).
}
$$

这条约束表示：所有影响参考前缀 $x$ 完成的高优先级相关执行，都发生在长度为 $x+\delta$ 的局部真实窗口内。因此，它既不能超过该局部窗口内的 certified carry-in 工作量上界，也不能超过单个顺序任务在该局部窗口中的最大执行容量。第 10.1 节将该事实单独形式化为局部窗口包含引理：窗口之后释放或执行的高优先级作业不能影响当前参考前缀是否能在 $x+\delta$ 内完成。

## 6.4 纯算力等待阶段容量约束

纯算力参考前缀中，目标等待长度为 $x-z$。这 $x-z$ 个参考等待单位必须由高优先级执行填满全部 $M$ 个处理器。因此要求

$$
\boxed{
\sum_{\tau_i\in hp(k)}a_i=M(x-z).
}
$$

同时，由顺序任务约束，单个高优先级任务在 $x-z$ 个参考等待单位中最多执行 $x-z$ 个单位：

$$
\boxed{
a_i\le x-z,
\qquad \forall \tau_i\in hp(k).
}
$$

本版不再额外使用形如 $[L(x,\delta)-C_k+1]^+$ 的局部截断。原因是，局部参考前缀 $x$ 并不一定包含目标作业的全部 $C_k$ 个执行单位；当 $z<C_k$ 时，在长度 $L(x,\delta)$ 的局部窗口内强行为剩余目标执行预留 $C_k$ 个单位可能排除真实可发生的高优先级等待模式。因此，v21 的第一版只保留上面的顺序容量约束 $a_i\le x-z$，以及第 6.3 节的局部总工作量约束 $a_i+b_i+u_i\le\min\{W_i^\Theta(L),L\}$。

## 6.5 目标执行阶段并发容量约束

目标任务已经执行 $z$ 个单位，因此高优先级并发执行满足

$$
b_i\le z,
\qquad \forall \tau_i\in hp(k).
$$

低优先级执行量满足

$$
c_j\in\mathbb Z_{\ge0},
\qquad
c_j\le W_j^D(L(x,\delta)),
\qquad
c_j\le z,
\qquad \forall \tau_j\in lp(k).
$$

其中 $W_j^D(L(x,\delta))$ 是对低优先级任务在局部窗口中的保守工作量上界。仍然使用 $D_j$ 而不是低优先级响应时间上界，是为了避免递推循环。

目标任务执行时占用一个核心，所有其他并发任务最多占用剩余 $M-1$ 个核心，所以要求

$$
\boxed{
\sum_{\tau_i\in hp(k)}b_i
+
\sum_{\tau_j\in lp(k)}c_j
\le
(M-1)z.
}
$$

## 6.6 局部额外偏移容量约束

$v20.4$ 使用窗口级约束

$$
\sum_i u_i\le M[w-x]^+.
$$

v21 改为局部偏移约束

$$
\boxed{
\sum_{\tau_i\in hp(k)}u_i\le M\delta.
}
$$

原因是：对于参考前缀 $x$，局部真实覆盖窗口长度为 $x+\delta$。其中 $x$ 个单位对应纯算力参考进度，剩下最多 $\delta$ 个单位是不推进参考进度的额外时间。所有 $u_i$ 都是未被参考进度计入的高优先级额外先行执行，因此只能发生在这 $\delta$ 个额外时间单位内。每个单位时间最多有 $M$ 个处理器执行单位，所以总量必然不超过 $M\delta$。

该约束不表示这些 $u_i$ 在时间账中可以除以 $M$。v21 仍然保留

$$
U(\omega)=\sum_i u_i
$$

作为高优先级额外先行执行造成的保守时间偏移。约束 $\sum_i u_i\le M\delta$ 只是排除无法嵌入局部窗口处理器容量的虚构变量组。

---

# 7. 局部时间—能量需求

对任意

$$
\omega\in\Omega_k^{\Theta,\mathrm{loc}}(w,x,\delta),
$$

定义额外高优先级先行执行量

$$
\boxed{
U(\omega)=\sum_{\tau_i\in hp(k)}u_i.
}
$$

定义该变量组对应的能量需求

$$
\boxed{
E_k^\Theta(\omega)
=
z\hat P_k
+
\sum_{\tau_i\in hp(k)}(a_i+b_i+u_i)\hat P_i
+
\sum_{\tau_j\in lp(k)}c_j\hat P_j.
}
$$

其中 $a_i,b_i,c_j$ 对应的执行时间已经被参考进度 $x$ 计入，只有 $u_i$ 对应的高优先级先行执行没有被 $x$ 计入。即使真实系统中某些 $u_i$ 可能并行发生，将它们串行化计入 $U(\omega)$ 也是安全的。

对固定变量组 $\omega$，相对于参考进度 $x$，所需额外偏移为

$$
\Delta_k^{\Theta,\mathrm{loc}}(x,\omega)
=
\max\left\{
U(\omega),
\left[
\beta_{l,\mathbb Z}^{-1}
\left([E_k^\Theta(\omega)-E_0]^+\right)
-x
\right]^+
\right\}.
$$

第一项覆盖未被参考进度计入的高优先级额外先行执行时间；第二项覆盖为了支付累计能量需求而可能需要的额外充能等待时间。

---

# 8. 内层局部闭合函数

对固定 $w,x,\delta$，若

$$
\Omega_k^{\Theta,\mathrm{loc}}(w,x,\delta)=\emptyset,
$$

则该 $\delta$ 不能作为参考前缀 $x$ 的闭合证书。这里不能把空集最大值解释为 0，否则会错误地把过小的局部窗口当作已经闭合。

若局部可行集合非空，则定义

$$
G_k^{\Theta,\mathrm{loc}}(w,x,\delta)
=
\max_{\omega\in\Omega_k^{\Theta,\mathrm{loc}}(w,x,\delta)}
\Delta_k^{\Theta,\mathrm{loc}}(x,\omega).
$$

对给定 $w$ 和 $x$，定义最小局部闭合偏移：

$$
\boxed{
\delta_x^\star(w)
=
\min\left\{
\delta\in\mathbb Z_{\ge0}
\;\middle|\;
\Omega_k^{\Theta,\mathrm{loc}}(w,x,\delta)\ne\emptyset
\text{ and }
G_k^{\Theta,\mathrm{loc}}(w,x,\delta)\le\delta
\right\}.
}
$$

若不存在有限整数 $\delta$ 满足该条件，则记

$$
\delta_x^\star(w)=\infty.
$$

最后定义局部窗口能量偏移上界：

$$
\boxed{
B_k^{E,\Theta,\mathrm{loc}}(w)
=
\max_{1\le x\le A_k^\Theta(w)}
\delta_x^\star(w).
}
$$

于是 v21 的主函数为

$$
\boxed{
F_k^{\Theta,\mathrm{loc}}(w)
=
A_k^\Theta(w)+B_k^{E,\Theta,\mathrm{loc}}(w).
}
$$

## 8.1 有限局部闭合搜索引理（Finite Local Closure Search Lemma）

**引理。** 对固定的候选窗口 $w$ 和参考前缀 $x$，v21 不要求一定存在满足

$$
G_k^{\Theta,\mathrm{loc}}(w,x,\delta)\le\delta
$$

的局部闭合偏移。若这样的偏移不存在，则当前 $x$ 在候选 $w$ 下不可闭合，当前 $w$ 不能证明目标任务。若闭合偏移集合非空，则最小闭合偏移 $\delta_x^\star(w)$ 存在；实现只需在有限整数区间

$$
0\le\delta\le\delta_{cap}(w),
\qquad
\delta_{cap}(w)=w-A_k^\Theta(w)
$$

内搜索用于判定当前候选窗口 $w$ 是否闭合。若 $A_k^\Theta(w)>w$，则 $\delta_{cap}(w)<0$，当前候选窗口连纯算力参考进度都无法容纳，必然不可能满足外层闭合条件；此时无需进入内层 $\delta$ 搜索，直接将该 $w$ 判定为不可证明。

**证明。** 外层闭合条件为

$$
A_k^\Theta(w)+B_k^{E,\Theta,\mathrm{loc}}(w)\le w.
$$

若 $A_k^\Theta(w)>w$，则左端已经大于 $w$，外层闭合条件不可能成立。以下只需考虑 $A_k^\Theta(w)\le w$ 的候选窗口。此时若某个前缀 $x$ 的最小闭合偏移大于

$$
w-A_k^\Theta(w),
$$

即使该前缀在更大 $\delta$ 上可以局部闭合，也无法使当前候选窗口 $w$ 通过外层闭合测试。所以，对固定 $w$ 的可证明性判定而言，只需考虑有限集合

$$
\{0,1,\ldots,w-A_k^\Theta(w)\}.
$$

在该有限整数集合上，若满足

$$
\Omega_k^{\Theta,\mathrm{loc}}(w,x,\delta)\ne\emptyset
$$

且

$$
G_k^{\Theta,\mathrm{loc}}(w,x,\delta)\le\delta
$$

的 $\delta$ 存在，则其最小值存在。若不存在，则该前缀在当前 $w$ 下没有闭合证书，算法安全地返回 unproven，而不是输出一个乐观上界。

该引理只保证闭合搜索有限终止，不声称每个 $w,x$ 都一定存在闭合偏移。

---

# 9. v21 RTA 判定

对目标任务 $\tau_k$，v21 定义响应时间上界为

$$
\boxed{
R_k^{\Theta,\mathrm{loc},UB}
=
\min\left\{
 w\in[C_k,D_k]\cap\mathbb Z
 \;\middle|\;
 F_k^{\Theta,\mathrm{loc}}(w)\le w
\right\}.
}
$$

若该集合为空，则 v21 不能证明 $\tau_k$ 在截止期内可调度。

与 v20.4 一样，v21 按固定优先级从高到低递推认证。只有当所有高优先级任务 $\tau_i\in hp(k)$ 已经被证明

$$
R_i^{\Theta,\mathrm{loc},UB}\le D_i
$$

时，才对它们使用 certified carry-in 参数 $\Theta_i=R_i^{\Theta,\mathrm{loc},UB}$。若某个高优先级任务未能被认证，则 taskset-level sufficient test 不能继续声称更低优先级任务被该 certified-carry-in 版本证明。实现中可以 fallback 到 $W_i^D$ 得到更保守的单任务结果，但论文中的 certified taskset proof 应坚持递推认证前提。

---

# 10. 正确性证明

## 10.1 局部窗口包含引理（Local Window Containment Lemma）

**引理。** 固定参考前缀 $x$ 和候选局部偏移 $\delta$。若要判断目标作业释放后纯算力参考前缀 $x$ 是否能在真实时间区间

$$
[r_k,\ r_k+x+\delta]
$$

内完成，则所有能够在该时间界之前造成阻塞、执行竞争、并发能耗或额外充能等待的相关执行，都包含在该长度为 $x+\delta$ 的局部窗口内。释放时间晚于 $r_k+x+\delta$ 的作业，或只在该时刻之后执行的作业，不能作为该前缀在 $x+\delta$ 内未完成的原因。

**证明。** 参考前缀 $x$ 的局部判定只关心区间

$$
[r_k,\ r_k+x+\delta]
$$

结束之前目标参考进度是否已经推进到 $x$。任何在该区间之后释放的高优先级作业，在区间内不可执行；任何虽然已经释放但只在区间之后执行的作业，也不能在区间内占用处理器或消耗能量。因此它们不可能阻止参考前缀 $x$ 在该局部窗口内完成。

需要强调的是，局部窗口上界覆盖的不是“最终完成参考前缀 $x$ 所需的所有未来执行”。如果参考前缀 $x$ 未能在 $x+\delta$ 内完成，那么时间界之后当然可能还有执行参与该前缀的最终完成；但是这些未来执行不能解释为什么该前缀没有在时间界 $r_k+x+\delta$ 之前完成。证明中需要覆盖的是在该时间界之前已经释放、已经执行、已经造成处理器竞争、已经造成能量消耗，或者已经足以形成能量等待的前缀级阻塞/需求证书。

因此，所有会阻止或延迟该前缀在局部时间界内完成的因素，必然表现为该局部窗口内的执行、阻塞或能量等待：高优先级执行可能占用处理器或先于目标推进；低优先级执行只可能在目标执行阶段与目标并发而消耗能量；能量等待只可能由该窗口内需要支付的执行需求触发。于是，用长度 $x+\delta$ 的工作量上界 $W_i^\Theta(x+\delta)$ 和 $W_j^D(x+\delta)$ 覆盖这些相关执行是安全的。

该引理说明，v21 将完整窗口工作量

$$
W_i^\Theta(w)
$$

替换为局部窗口工作量

$$
W_i^\Theta(x+\delta)
$$

时，只排除了不会影响当前参考前缀闭合的窗口外工作，而不会漏掉能够在该时间界之前影响该前缀完成的窗口内工作。

## 10.2 局部工作量上界安全性

考虑任意参考前缀 $x$。若该参考前缀在真实调度中需要长度 $x+\delta$ 的局部时间窗口才能完成，则所有用于解释该前缀完成之前的相关执行，都发生在长度为 $x+\delta$ 的真实时间区间内。

对已认证高优先级任务 $\tau_i\in hp(k)$，在任意长度为 $x+\delta$ 的区间内，其执行工作量不超过 $W_i^\Theta(x+\delta)$。同时，顺序任务在该区间内最多执行 $x+\delta$ 个单位。因此

$$
a_i+b_i+u_i
\le
\min\{W_i^\Theta(x+\delta),x+\delta\}.
$$

对低优先级任务 $\tau_j\in lp(k)$，本文不使用其响应时间上界，而保守使用 deadline-parameterized 工作量上界，因此

$$
c_j\le W_j^D(x+\delta).
$$

需要注意，低优先级 bound 的安全性并不是孤立单任务语境下无条件成立的；它在第 10.6 节的 taskset-level first-counterexample 证明中闭合。也就是说，只有在全系统第一个反例之前没有低优先级旧作业任意积压时，$W_j^D(x+\delta)$ 才能作为局部窗口内低优先级并发需求的安全上界。

因此，在第 10.6 节的任务集级证明语境下，v21 的局部工作量约束不会低估任何能够发生在该局部窗口内的真实执行量。

## 10.3 局部容量约束安全性

参考进度 $x$ 中，目标等待阶段由 $M(x-z)$ 个高优先级执行单位填满，因此有

$$
\sum_{\tau_i\in hp(k)}a_i=M(x-z).
$$

单个顺序高优先级任务在 $x-z$ 个参考等待单位内最多贡献 $x-z$ 个执行单位，所以

$$
a_i\le x-z.
$$

目标执行阶段长度为 $z$，目标自身占用一个处理器，因此其他任务合计最多占用 $(M-1)z$ 个处理器单位：

$$
\sum_{\tau_i\in hp(k)}b_i+
\sum_{\tau_j\in lp(k)}c_j
\le (M-1)z.
$$

最后，$u_i$ 只统计未被参考进度 $x$ 计入的高优先级额外先行执行。若参考前缀 $x$ 被长度 $x+\delta$ 的真实局部窗口覆盖，则未推进参考进度的额外时间至多为 $\delta$。每个额外时间单位最多容纳 $M$ 个执行单位，因此

$$
\sum_{\tau_i\in hp(k)}u_i\le M\delta.
$$

该约束只排除局部处理器容量无法容纳的变量组，不改变 $U(\omega)=\sum_i u_i$ 的保守时间解释。

## 10.4 局部闭合偏移安全性

若某个 $\delta$ 满足

$$
\Omega_k^{\Theta,\mathrm{loc}}(w,x,\delta)\ne\emptyset
$$

且

$$
G_k^{\Theta,\mathrm{loc}}(w,x,\delta)\le\delta,
$$

则对所有

$$
\omega\in\Omega_k^{\Theta,\mathrm{loc}}(w,x,\delta)
$$

都有

$$
\delta\ge U(\omega),
$$

并且

$$
x+
\delta
\ge
\beta_{l,\mathbb Z}^{-1}
\left([E_k^\Theta(\omega)-E_0]^+\right).
$$

由离散反函数定义和 $\beta_l$ 单调性可得

$$
E_0+\beta_l(x+\delta)
\ge
E_k^\Theta(\omega).
$$

因此，长度 $x+\delta$ 的真实时间区间同时足以覆盖：

1. 纯算力参考进度 $x$；
2. 未被参考进度计入的高优先级额外先行执行 $U(\omega)$；
3. 支付该前缀相关执行所需的保守能量需求 $E_k^\Theta(\omega)$。

在 A6 的在线能量服务曲线语义和 A8 的单位时间可执行性条件下，上述时间账和能量账覆盖可以推出：该参考前缀不会因为未计入的高优先级先行执行或充能等待而延长到 $x+\delta$ 之外。

## 10.5 前缀阻塞证书与局部转换引理（taskset-level first-counterexample 语境）

**引理。** 在第 10.6 节的 taskset-level first-counterexample 语境中，对固定 $w$ 和 $x$，若存在某个非负整数 $\delta$ 使得

$$
\Omega_k^{\Theta,\mathrm{loc}}(w,x,\delta)\ne\emptyset
$$

且

$$
G_k^{\Theta,\mathrm{loc}}(w,x,\delta)\le\delta,
$$

则实际 ASAP-BLOCK 调度完成纯算力参考进度长度 $x$ 的时间不超过 $x+\delta$。

**证明。** 采用反证。假设参考前缀 $x$ 不能在真实时间 $x+\delta$ 内完成。令

$$
I=[r_k,\ r_k+x+\delta]
$$

为该局部时间界之前的真实调度窗口。

由第 10.1 节的局部窗口包含引理，窗口 $I$ 之后释放或执行的作业不能作为“参考前缀 $x$ 未能在 $x+\delta$ 内完成”的原因。因此，若该前缀在该时间界内失败，则失败原因必须能够由 $I$ 内的处理器竞争、前缀阻塞、并发能耗和能量等待形成一个**前缀级阻塞/需求证书**。下面将该证书记为

$$
\omega^\star=(z,a_i,b_i,u_i,c_j).
$$

这里需要特别说明：$\omega^\star$ 不是声称“真实窗口 $I$ 内已经完整观察到了完成整个参考前缀 $x$ 所需的所有执行单位”。当参考前缀尚未完成时，某些最终帮助它完成的执行可能发生在窗口之后；这些未来执行不应被计入当前局部证书。$\omega^\star$ 的作用是保守刻画在时间界 $r_k+x+\delta$ 之前已经足以解释未完成状态的前缀级需求。换言之，它是一个阻塞证书，而不是完整完成历史。

相应地，证书中的 $E_k^\Theta(\omega^\star)$ 表示完成或消除该前缀级阻塞所需支付的保守能量需求上界，不要求其中每个执行单位都已经在窗口 $I$ 内完成。若这些需求不能在 $x+\delta$ 内被在线支付，则这正是导致参考前缀未能在局部时间界内闭合的能量障碍；局部闭合条件 $G_k^{\Theta,\mathrm{loc}}(w,x,\delta)\le\delta$ 的作用就是排除这种障碍。

该证书按如下方式构造和解释。

1. **目标参考执行量。** 令 $z$ 为纯算力参考前缀 $x$ 中属于目标作业 $\tau_k$ 的参考执行量。该量由参考进度定义决定，因此

$$
z\in Z_k^\Theta(w,x).
$$

2. **参考等待阶段的高优先级需求。** 参考前缀中目标未执行的部分长度为 $x-z$。在纯算力参考解释中，这些等待单位由高优先级需求填满。令 $a_i$ 表示归属于高优先级任务 $\tau_i$ 的这部分前缀级需求。于是

$$
\sum_{\tau_i\in hp(k)}a_i=M(x-z),
$$

且由顺序任务约束，单个任务在 $x-z$ 个参考等待单位中最多贡献 $x-z$ 个单位：

$$
a_i\le x-z.
$$

这些 $a_i$ 不需要全部解释为窗口 $I$ 内已经完成的执行；当真实前缀尚未完成时，它们表示在局部时间界之前足以阻止目标参考进度达到 $x$ 的高优先级处理器需求证书。如果这样的需求无法嵌入长度 $x+\delta$ 的局部工作量和容量约束，则相应 $\delta$ 不会形成可行闭合证书。

3. **目标参考执行阶段的并发需求。** 在目标作业已经执行的 $z$ 个参考单位中，与目标作业并发并影响能量账的高优先级需求计入 $b_i$，低优先级并发需求计入 $c_j$。由于目标作业本身占用一个核心，其他任务在这 $z$ 个单位中最多使用 $(M-1)z$ 个处理器单位，因此

$$
\sum_{\tau_i\in hp(k)}b_i+
\sum_{\tau_j\in lp(k)}c_j
\le (M-1)z.
$$

并且由顺序任务约束，

$$
b_i\le z,
\qquad
c_j\le z.
$$

4. **未被参考进度吸收的高优先级额外需求。** 窗口 $I$ 中任何没有被参考进度 $x$ 吸收、但会优先于目标参考推进、造成额外处理器等待或能量等待的高优先级需求，计入 $u_i$。这些需求只能被放入真实时间相对参考进度的额外部分中；该额外部分长度至多为 $\delta$，每个单位时间最多容纳 $M$ 个处理器执行单位，因此

$$
\sum_{\tau_i\in hp(k)}u_i\le M\delta.
$$

上述四类变量覆盖了所有能够在局部时间界之前解释参考前缀失败的需求：目标等待阶段的高优先级处理器需求进入 $a_i$，目标执行阶段的并发能耗需求进入 $b_i,c_j$，未被参考进度吸收的高优先级额外需求进入 $u_i$。窗口之后才释放或执行的作业不能作为当前局部失败的原因，已经由局部窗口包含引理排除。

接下来验证该证书可以被局部可行集合覆盖。所有被计入 $a_i+b_i+u_i$ 的高优先级需求，都必须是在长度 $x+\delta$ 的局部窗口内可释放、可执行、可阻塞或可造成能量等待的需求。由于 $\tau_i$ 已按优先级顺序认证，其在任意长度为 $x+\delta$ 的局部窗口内能够贡献的相关工作量不超过 $W_i^\Theta(x+\delta)$；又因为任务是顺序任务，它在长度 $x+\delta$ 的窗口内最多执行 $x+\delta$ 个单位。因此

$$
a_i+b_i+u_i
\le
\min\{W_i^\Theta(x+\delta),x+\delta\}.
$$

同理，每个低优先级任务在该局部窗口内能够贡献给 $c_j$ 的并发需求不超过保守工作量上界 $W_j^D(x+\delta)$，所以

$$
c_j\le W_j^D(x+\delta).
$$

因此，如果参考前缀 $x$ 不能在 $x+\delta$ 内完成，则存在一个由窗口 $I$ 内因果相关需求形成的前缀级证书

$$
\omega^\star\in\Omega_k^{\Theta,\mathrm{loc}}(w,x,\delta).
$$

由于假设

$$
G_k^{\Theta,\mathrm{loc}}(w,x,\delta)
\le\delta,
$$

对该证书 $\omega^\star$ 必有

$$
\delta\ge U(\omega^\star)
$$

以及

$$
E_0+\beta_l(x+\delta)
\ge E_k^\Theta(\omega^\star).
$$

第一条不等式说明 $\delta$ 覆盖了所有未被参考进度吸收的高优先级额外需求；第二条不等式结合 A6 说明，在长度 $x+\delta$ 的真实区间内，窗口初始能量与在线收集能量足以按时间顺序支付该证书代表的保守能量需求。再结合 A8 的单位时间前缀执行可支付性，ASAP-BLOCK 不会因为该证书中的处理器需求、额外先行需求或能量需求而产生超过 $\delta$ 的额外延迟。

这与 $\omega^\star$ 是阻止参考前缀 $x$ 在 $x+\delta$ 内完成的证书相矛盾。因此，若局部闭合条件成立，则参考前缀 $x$ 必能在真实时间 $x+\delta$ 内完成。引理成立。

## 10.6 任务集级主定理（first-counterexample 形式）

前面各节给出的局部前缀引理本身只说明：在给定的局部窗口、工作量上界和容量约束都安全覆盖真实阻塞/需求证书时，闭合偏移能够保证对应参考前缀完成。为了把这一局部结论提升为完整 RTA 的安全性，主定理必须放在 taskset-level first-counterexample 语境中。特别是，v21 的局部变量集合仍然包含低优先级并发项

$$
c_j\le W_j^D(x+\delta),\qquad \tau_j\in lp(k),
$$

这条约束的安全性依赖于：在被分析的反例窗口之前，低优先级任务不存在任意旧 backlog。该性质由全系统第一个反例保证，而不是由孤立单任务分析自动保证。

**定理（任务集级充分性）。** 设任务集按固定优先级从高到低递推计算 v21 局部窗口 RTA。对每个任务 $\tau_k$，若在所有高优先级任务已经认证的前提下，存在

$$
R_k^{\Theta,\mathrm{loc},UB}
=
\min\left\{
 w\in[C_k,D_k]\cap\mathbb Z
 \mid
 F_k^{\Theta,\mathrm{loc}}(w)\le w
\right\}
$$

并且所有任务最终均满足

$$
R_k^{\Theta,\mathrm{loc},UB}\le D_k,
$$

则该任务集在 ASAP-BLOCK 调度下可调度。特别地，每个作业的响应时间均不超过其任务的候选上界 $R_k^{\Theta,\mathrm{loc},UB}$。

**证明。** 采用全系统第一个反例证明。反设结论不成立，则存在某个作业在 ASAP-BLOCK 下释放后经过其候选上界仍未完成。令 $J_k$ 为全系统中第一个这样的作业，释放时刻为 $r_k$，候选上界为

$$
w=R_k^{\Theta,\mathrm{loc},UB},
$$

并令

$$
t^\star=r_k+w.
$$

由 $J_k$ 的选择可知，在 $t^\star$ 之前，不存在任何任务的作业已经释放后经过自己的候选响应时间上界仍未完成。又因为所有任务均满足

$$
R_j^{\Theta,\mathrm{loc},UB}\le D_j,
$$

所以在 $t^\star$ 之前也不存在已经到达绝对截止期而未完成的作业。这个 first-counterexample 性质将同时用于高优先级 certified carry-in 和低优先级 deadline-parameterized workload bound 的安全性说明。

首先，对任意高优先级任务 $\tau_i\in hp(k)$，由于递推认证已经给出

$$
R_i^{\Theta,\mathrm{loc},UB}\le D_i,
$$

且在第一个反例 $J_k$ 之前不存在高优先级任务超过其候选上界，故在分析 $J_k$ 的任意局部窗口时，$\tau_i$ 的 carry-in 滞留可由

$$
\Theta_i=R_i^{\Theta,\mathrm{loc},UB}
$$

安全覆盖。因此，v21 局部窗口中对高优先级任务使用

$$
W_i^\Theta(x+\delta)
$$

是安全的。

其次，对任意低优先级任务 $\tau_j\in lp(k)$，v21 并不使用其 certified response-time carry-in，而是使用 deadline-parameterized bound

$$
W_j^D(x+\delta).
$$

这一步同样需要 first-counterexample 语境。由于 $J_k$ 是全系统第一个违反候选上界的作业，且所有任务均已证明 $R_j^{\Theta,\mathrm{loc},UB}\le D_j$，在 $t^\star$ 之前没有任何低优先级作业错过截止期。结合 constrained-deadline 假设 $D_j\le T_j$，局部窗口起点之前不可能存在任意多已经过期却仍未完成的低优先级旧作业；任意能在局部窗口中与目标作业并发并消耗能量的低优先级执行，至多表现为由相对截止期 $D_j$ 覆盖的 carry-in 与窗口内释放工作量。因此，

$$
c_j\le W_j^D(x+\delta)
$$

安全覆盖了局部窗口中低优先级任务能够贡献给并发能耗项的工作量。注意，这里并没有声称低优先级任务的 certified response-time bound 被用于分析 $\tau_k$；使用 $W_j^D$ 的目的正是避免对低优先级任务响应时间上界产生循环依赖。

现在考虑目标作业 $J_k$ 的完整参考前缀。由 $w=R_k^{\Theta,\mathrm{loc},UB}$ 的定义，有

$$
F_k^{\Theta,\mathrm{loc}}(w)
=
A_k^\Theta(w)+B_k^{E,\Theta,\mathrm{loc}}(w)
\le w.
$$

对任意

$$
1\le x\le A_k^\Theta(w),
$$

由 $B_k^{E,\Theta,\mathrm{loc}}(w)$ 的定义可知，对应的局部闭合偏移满足

$$
\delta_x^\star(w)
\le
B_k^{E,\Theta,\mathrm{loc}}(w).
$$

在上述 first-counterexample 语境下，高优先级的 $W_i^\Theta(x+\delta)$、低优先级的 $W_j^D(x+\delta)$、处理器容量约束和能量服务假设 A6/A8 均可安全覆盖第 10.5 节中的前缀级阻塞/需求证书。因此，由第 10.5 节的局部转换引理，参考前缀 $x$ 可在真实时间

$$
x+\delta_x^\star(w)
$$

内完成，从而也可在

$$
x+B_k^{E,\Theta,\mathrm{loc}}(w)
$$

内完成。

取完整参考前缀

$$
x=A_k^\Theta(w).
$$

根据参考构造，当 $x=A_k^\Theta(w)$ 时目标作业的参考执行量满足

$$
z=C_k,
$$

即目标作业已经获得全部 $C_k$ 个执行单位。又由外层闭合条件

$$
A_k^\Theta(w)+B_k^{E,\Theta,\mathrm{loc}}(w)
\le w,
$$

可知 $J_k$ 必须在 $r_k+w=t^\star$ 之前完成。这与 $J_k$ 是全系统第一个释放后经过候选上界仍未完成的作业矛盾。因此不存在这样的反例作业，所有作业的响应时间均不超过其候选上界。由于所有任务均满足 $R_k^{\Theta,\mathrm{loc},UB}\le D_k$，任务集在 ASAP-BLOCK 下可调度。定理得证。

**说明。** 如果脱离 taskset-level first-counterexample 语境，仅想声明一个孤立单任务上界，则不能无条件保留低优先级约束 $c_j\le W_j^D(x+\delta)$，因为低优先级任务可能携带任意旧 backlog。本文选择保留该有用的局部工作量约束，并将主正确性结论表述为任务集级充分性定理；这也是 v20.4 以来 certified-carry-in 路线的自然闭合方式。

---

# 11. v21 与 v20.4 的连接关系

v21 应当被理解为 v20.4 的局部窗口加强版，但本修复版不再把“v21 无条件不弱于 v20.4”作为无需额外前提的定理。原因是：第 8 节采用了保守的空集处理；若某个过小局部窗口下 $\Omega_k^{\Theta,\mathrm{loc}}(w,x,\delta)$ 为空，则该 $\delta$ 不被视为闭合证书。这种处理避免了把空集合最大值误解释为 0，但也意味着工程实现中可能出现比纯最大化包含关系更保守的情况。

下面给出可安全使用的连接关系。令 v20.4 的能量偏移为

$$
B_k^{E,\Theta}(w),
$$

v21 的局部窗口偏移为

$$
B_k^{E,\Theta,\mathrm{loc}}(w).
$$

假设某个候选窗口 $w$ 在 v20.4 下已经闭合：

$$
A_k^\Theta(w)+B_k^{E,\Theta}(w)\le w.
$$

对任意

$$
1\le x\le A_k^\Theta(w),
$$

有

$$
x+B_k^{E,\Theta}(w)
\le
A_k^\Theta(w)+B_k^{E,\Theta}(w)
\le w.
$$

因此

$$
W_i^\Theta(x+B_k^{E,\Theta}(w))
\le
W_i^\Theta(w),
$$

并且

$$
W_j^D(x+B_k^{E,\Theta}(w))
\le
W_j^D(w).
$$

同时，v20.4 proof-strengthened 版本已经证明，在闭合窗口下任意真实前缀级变量组满足

$$
\sum_i u_i
\le
M B_k^{E,\Theta}(w)
\le
M[w-x]^+.
$$

所以，当取

$$
\delta=B_k^{E,\Theta}(w)
$$

且局部可行集合非空时，v21 的局部工作量窗口 $x+\delta$ 不大于 v20.4 的完整窗口 $w$，局部容量约束 $\sum_i u_i\le M\delta$ 也与 v20.4 闭合窗口中的前缀容量证明一致。此时，v21 对同一前缀的最大化域不大于 v20.4 的相应最大化域，因此 v20.4 的闭合偏移可以作为 v21 内层闭合搜索的候选证书。

由此得到一个带前提的非劣关系：

$$
\boxed{
\text{若 }\delta=B_k^{E,\Theta}(w)\text{ 对每个 }x\text{ 都给出非空局部闭合候选，}
\text{则 }B_k^{E,\Theta,\mathrm{loc}}(w)\le B_k^{E,\Theta}(w).
}
$$

进一步有

$$
F_k^{\Theta,\mathrm{loc}}(w)\le F_k^\Theta(w)
$$

在上述同一前提下成立。

v20.4 应作为当前已完整证明并经过实验检查的 sufficient baseline。v21 则是保持同一充分上界证明思路的局部窗口加强候选：理论上它通过更小的局部窗口排除与当前前缀无关的工作量，工程上仍需要通过 smoke test 检查 soundness、timeout、空集处理和与 v20.4 的一致性。

这说明 v21 的设计意图和主要工作量约束均是对 v20.4 的局部化收紧；但在正式实现和实验中，仍应保留 sanity check，而不能把无条件非劣性作为不经验证的实现假设。若出现某个任务的 v21 bound 大于 v20.4 bound，应优先检查局部窗口、空集处理、$\delta$ 搜索范围、$z$ 的枚举方式或 certified carry-in 参数是否与定义一致。若实现因空集处理或 timeout 导致 v21 更保守，则可以在实验中报告为工程保守性，并回退到 v20.4 作为 baseline。

# 12. 单调性与计算方式

对固定 $w$ 和 $x$，随着 $\delta$ 增大，局部窗口

$$
L(x,\delta)=x+\delta
$$

单调增大。因此

$$
W_i^\Theta(L(x,\delta))
$$

和

$$
W_j^D(L(x,\delta))
$$

均单调不减，局部变量集合

$$
\Omega_k^{\Theta,\mathrm{loc}}(w,x,\delta)
$$

在非空后只会扩张，不会缩小。因此，在局部可行集合非空的区间内，

$$
G_k^{\Theta,\mathrm{loc}}(w,x,\delta)
$$

关于 $\delta$ 单调不减。

单调性本身并不表示闭合一定存在，因为闭合条件是

$$
G_k^{\Theta,\mathrm{loc}}(w,x,\delta)\le\delta,
$$

右侧的 $\delta$ 也在增长。它真正保证的是跳跃式搜索的正确性。若在某个 $\delta$ 上有

$$
g=G_k^{\Theta,\mathrm{loc}}(w,x,\delta)>\delta,
$$

则任何

$$
\delta'\in[\delta,g)
$$

都不可能闭合。原因是 $G$ 单调不减，因此

$$
G_k^{\Theta,\mathrm{loc}}(w,x,\delta')
\ge
G_k^{\Theta,\mathrm{loc}}(w,x,\delta)
=g
>
\delta'.
$$

所以这些中间值均不满足 $G(\delta')\le\delta'$，实现可以安全地直接跳到 $g$。结合第 8.1 节的有限局部闭合搜索引理，内层搜索不假定闭合一定存在；若在有限上界内找不到闭合证书，则安全返回当前候选 $w$ 不可证明。伪代码如下：

```text
close_delta(w, x):
    delta = 0
    while delta <= delta_cap:
        if Omega_loc(w, x, delta) is empty:
            delta = delta + 1
            continue

        g = G_loc(w, x, delta)
        if g <= delta:
            return delta

        delta = g

    return no closure within current candidate w
```

其中

$$
\delta_{cap}=w-A_k^\Theta(w)
$$

只作为检查候选窗口 $w$ 是否闭合时的实现上界。理论上的 $\delta_x^\star(w)$ 可以定义在所有非负整数上；但若最小闭合偏移已经超过

$$
w-A_k^\Theta(w),
$$

则当前 $w$ 必然不能满足

$$
A_k^\Theta(w)+B_k^{E,\Theta,\mathrm{loc}}(w)
\le w.
$$

所以实现中可以在 $\delta>\delta_{cap}$ 时立即判定当前 $w$ 不可证明。

外层建议采用候选窗口扫描形式：

```text
for w in C_k, C_k+1, ..., D_k:
    A = A_theta(w)
    if A > w:
        continue

    B = 0
    delta_cap = w - A

    for x in 1, ..., A:
        delta_x = close_delta(w, x)
        if no closure:
            fail current w
        B = max(B, delta_x)

    if A + B <= w:
        return w

return unproven
```

虽然 v20.4 可以自然写成外层固定点迭代，v21 由于加入内部 $\delta$ 闭合与空集处理，工程实现上使用候选 $w$ 扫描更稳妥。若实现采用固定点迭代，也必须在每个候选 $w$ 上重新验证闭合条件

$$
F_k^{\Theta,\mathrm{loc}}(w)
\le w.
$$

---

# 13. 实现注意事项

v21 不应覆盖 v20.4 实现，建议保留两个版本并提供版本开关：

```text
--rta-version v20.4
--rta-version v21-local-window
```

或在代码中保留两个函数：

```text
compute_asap_block_rta_v20_4(...)
compute_asap_block_rta_v21_local_window(...)
```

必须增加以下 regression checks：

1. **Soundness guard。** 不允许出现 RTA proven 但 simulation rejected 的样本；不允许出现 observed max response time 超过 RTA bound 的样本。
2. **v20.4 consistency guard。** 在同一任务、同一 $E_0$、同一 certified carry-in 输入下，优先记录并检查 v21 bound 是否大于 v20.4 bound。若出现这种情况，不能直接判定 v21 不安全；应区分是定义中的保守空集处理、timeout/fallback，还是实现错误导致。
3. **Fallback guard。** 如果 v21 因 timeout 或实现限制无法完成，应允许回退到 v20.4，而不能输出更乐观但未经闭合验证的 bound。
4. **Empty-set guard。** 若 $\Omega_k^{\Theta,\mathrm{loc}}(w,x,\delta)$ 为空，该 $\delta$ 不能被视为闭合；不能把空集最大值当作 0。该处理是保守选择，可能使 v21 工程结果不满足无条件非劣于 v20.4 的性质。

建议缓存以下对象：

```text
W_i^Theta(L)
W_j^D(L)
beta_inverse(E)
G_loc(w,x,delta)
Omega feasibility summaries
```

其中 $W_i^\Theta(L)$、$W_j^D(L)$ 和 $\beta_{l,\mathbb Z}^{-1}$ 的缓存最重要。

---

# 14. v21 第一版不应加入的内容

为了保证 v21 的证明和实现风险可控，第一版只做局部窗口 fixed point，不做以下改动：

1. 不把

$$
U(\omega)=\sum_i u_i
$$

改成

$$
\left\lceil\frac{\sum_i u_i}{M}\right\rceil
$$

或类似并行压缩形式。

2. 不修改 $A_k^\Theta(w)$ 的纯算力排队项。
3. 不引入 adaptive scheduler。
4. 不引入 finite-battery overflow-aware service curve。
5. 不用低优先级任务的未认证响应时间上界替换 $W_j^D$。

这些方向可以作为后续 v21.1 或 v22 的扩展，但不应混入 v21 的第一版核心证明。

---

# 15. 建议实验计划

v21 先做小规模 smoke，而不是直接全量正式实验。建议第一阶段配置：

```text
seed = 424242
U = 0.1, 0.2, 0.3
每个 U 取 50 个 taskset
E0 = 0.25, 1.0
scheduler = ASAP-BLOCK
compare = v20.4 vs v21-local-window
```

重点检查：

| 指标 | 期望 |
|---|---|
| soundness violation | 必须为 0 |
| v21 bound > v20.4 bound | 理想为 0；若非 0，需要区分空集保守、timeout/fallback 与实现错误 |
| timeout | 不应明显恶化 |
| mean / median tightness | 应较 v20.4 下降 |
| v21 proven but v20.4 unproven | 若出现，说明 v21 对 proven ratio 有实际收益 |
| U=0.2 proven tasksets | 若从 0 变为非零，是最有价值的信号 |

若 smoke 显示 v21 tightness 明显下降，且 timeout 可控，再进入正式 RTA ablation：

```text
v20.4 vs v21-local-window
E0 sensitivity
same tasksets, same seeds, same simulation observations
```

若 v21 只略微降低 tightness，但不能提升 proven ratio，则可以作为附录或 future direction；若 v21 使 mean tightness 从 v20.4 的约 6 进一步降到 3--5，并在 $U=0.2$ 出现可证明 taskset，则值得替换 v20.4 成为论文主 RTA 版本。

---

# 16. 最终结论

v21 是 v20.4 的自然升级版。它保留 v20.4 已经证明安全的 certified carry-in 与容量约束，同时把每个参考前缀 $x$ 的工作量窗口从完整候选窗口 $w$ 缩小为局部覆盖窗口 $x+\delta$，并通过内部闭合偏移 $\delta_x^\star$ 保证局部时间账和能量账同时闭合。

核心公式为：

$$
\Omega_k^\Theta(w,x)
\quad\Longrightarrow\quad
\Omega_k^{\Theta,\mathrm{loc}}(w,x,\delta),
$$

$$
W_i^\Theta(w)
\quad\Longrightarrow\quad
W_i^\Theta(x+\delta),
$$

$$
\sum_i u_i\le M[w-x]^+
\quad\Longrightarrow\quad
\sum_i u_i\le M\delta,
$$

但仍然保留

$$
U(\omega)=\sum_i u_i.
$$

因此，v21 的紧化方式是安全地缩小可行变量集合，而不是不安全地压缩额外先行执行时间。在非空闭合候选和实现一致的条件下，v21 的局部最大化域应不大于 v20.4；在工程实现中仍需用 v20.4 consistency guard 检查是否出现更保守的结果。若 smoke test 显示局部闭合搜索稳定，v21 有机会进一步降低 RTA tightness、提高低负载到中低负载区间的可证明性。
