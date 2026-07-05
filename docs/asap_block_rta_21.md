# ASAP-BLOCK 局部窗口闭合搜索响应时间分析与正确性证明

---

# 1. 局部窗口分析的核心思想

完整窗口分析的主函数为

$$
F_k^\Theta(w)=A_k^\Theta(w)+B_k^{E,\Theta}(w),
$$

其中

$$
A_k^\Theta(w)=C_k+D_k^{P,\Theta}(w).
$$

完整窗口分析的保守性主要来自：对每一个纯算力参考前缀 $x\le A_k^\Theta(w)$，在构造能量需求变量组时都使用完整候选窗口 $w$ 的工作量上界，例如

$$
a_i+b_i+u_i\le \min\{W_i^\Theta(w),w\}.
$$

但是，完成参考前缀 $x$ 实际只需要考虑覆盖该前缀的真实局部时间窗口。如果该前缀需要额外偏移 $\delta$，则相关执行只发生在长度

$$
L(x,\delta)=x+\delta
$$

的局部窗口中。于是本文将上述约束局部化为

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

本文不修改纯算力参考项 $A_k^\Theta(w)$，只替换能量偏移项：

$$
B_k^{E,\Theta}(w)
\quad\Longrightarrow\quad
B_k^{E,\Theta,\mathrm{loc}}(w).
$$

因此本文的外层主函数为

$$
\boxed{
F_k^{\Theta,\mathrm{loc}}(w)
=
A_k^\Theta(w)+B_k^{E,\Theta,\mathrm{loc}}(w).
}
$$

---

# 2. 系统模型与沿用假设

本文采用如下系统模型与调度语义。系统包含 $M$ 个相同处理器核心，所有核心共享一个储能单元。任务集为

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

能量服务曲线定义如下。令 $\beta_l(\Delta)$ 表示任意长度为 $\Delta$ 的真实时间区间内系统至少能够在线收集并可用于执行的能量下界。假设 $\beta_l$ 单调不减且 $\beta_l(0)=0$。离散反函数定义为

$$
\beta_{l,\mathbb Z}^{-1}(E)
=
\min\{\Delta\in\mathbb Z_{\ge0}\mid E\le \beta_l(\Delta)\}.
$$

若不存在有限整数 $\Delta$ 满足该条件，则约定

$$
\beta_{l,\mathbb Z}^{-1}(E)=\infty.
$$

本文依赖以下语义边界：$\beta_l$ 必须是在线可用能量服务曲线，而不是仅在区间末端统计的离线累计收集量；若存在储能溢出，则 $\beta_l$ 必须已经扣除溢出损失；$E_0$ 表示任意分析窗口起点，即目标作业释放时刻，可保证存在的储能下界。

为避免后续证明中引用不清，本文显式列出两个能量语义假设。

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

因此，本文中的 certified carry-in 参数来自当前局部窗口递推认证结果。若实现中为了 fallback 使用其他已认证上界，需要在实验报告中单独说明。

由于 $\Theta_i\le D_i$，有

$$
W_i^\Theta(L)\le W_i^D(L).
$$

本文只对已经认证的高优先级任务使用 $W_i^\Theta$。低优先级任务仍使用 $W_j^D$，以避免对尚未分析的低优先级任务引入循环依赖。

---

# 5. 纯算力参考进度

本文采用如下纯算力参考进度定义。

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

# 6. 局部窗口变量集合

局部窗口分析的关键步骤发生在能量偏移分析中。对固定候选窗口 $w$、参考前缀 $x$ 和局部偏移 $\delta$，定义局部覆盖窗口长度

$$
\boxed{
L(x,\delta)=x+\delta.
}
$$

能量变量集合使用局部窗口 $L(x,\delta)$，而不是完整候选窗口 $w$。

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

本文将完整窗口约束局部化为

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

本文不额外使用形如 $[L(x,\delta)-C_k+1]^+$ 的局部截断。原因是，局部参考前缀 $x$ 并不一定包含目标作业的全部 $C_k$ 个执行单位；当 $z<C_k$ 时，在长度 $L(x,\delta)$ 的局部窗口内强行为剩余目标执行预留 $C_k$ 个单位可能排除真实可发生的高优先级等待模式。因此，本文只保留上面的顺序容量约束 $a_i\le x-z$，以及第 6.3 节的局部总工作量约束 $a_i+b_i+u_i\le\min\{W_i^\Theta(L),L\}$。

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

完整窗口分析使用窗口级约束

$$
\sum_i u_i\le M[w-x]^+.
$$

本文采用局部偏移约束

$$
\boxed{
\sum_{\tau_i\in hp(k)}u_i\le M\delta.
}
$$

原因是：对于参考前缀 $x$，局部真实覆盖窗口长度为 $x+\delta$。其中 $x$ 个单位对应纯算力参考进度，剩下最多 $\delta$ 个单位是不推进参考进度的额外时间。所有 $u_i$ 都是未被参考进度计入的高优先级额外先行执行，因此只能发生在这 $\delta$ 个额外时间单位内。每个单位时间最多有 $M$ 个处理器执行单位，所以总量必然不超过 $M\delta$。

该约束不表示这些 $u_i$ 在时间账中可以除以 $M$。本文仍然保留

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

于是本文的主函数为

$$
\boxed{
F_k^{\Theta,\mathrm{loc}}(w)
=
A_k^\Theta(w)+B_k^{E,\Theta,\mathrm{loc}}(w).
}
$$

## 8.1 有限局部闭合搜索引理（Finite Local Closure Search Lemma）

**引理。** 对固定的候选窗口 $w$ 和参考前缀 $x$，本文不要求一定存在满足

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

# 9. RTA 判定

对目标任务 $\tau_k$，本文定义响应时间上界为

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

若该集合为空，则本文不能证明 $\tau_k$ 在截止期内可调度。

本文按固定优先级从高到低递推认证。只有当所有高优先级任务 $\tau_i\in hp(k)$ 已经被证明

$$
R_i^{\Theta,\mathrm{loc},UB}\le D_i
$$

时，才对它们使用 certified carry-in 参数 $\Theta_i=R_i^{\Theta,\mathrm{loc},UB}$。若某个高优先级任务未能被认证，则 taskset-level sufficient test 不能继续声称更低优先级任务被该 certified-carry-in 分析证明。实现中可以 fallback 到 $W_i^D$ 得到更保守的单任务结果，但论文中的 certified taskset proof 应坚持递推认证前提。

---

# 10. 正确性证明

本节证明局部窗口分析给出的闭合条件是充分的。为了避免把“已经真实发生的执行历史”和“用于排除局部失败的需求证书”混在一起，下面先明确本文使用的证书语义。

对参考前缀 $x$ 和候选偏移 $\delta$，若该前缀未能在半开区间

$$
[r_k,\ r_k+x+\delta)
$$

内完成，则所有能够解释这一失败的因素，必须在该局部时间界之前已经产生因果影响。这样的因素包括：高优先级处理器竞争、ASAP-BLOCK 能量阻塞造成的高优先级额外先行执行、目标执行阶段的并发能耗，以及为支付这些执行需求而产生的充能等待。本文把这些因素聚合成一个前缀级阻塞/需求证书

$$
\omega=(z,a_i,b_i,u_i,c_j).
$$

该证书不是说窗口内已经完整观察到了目标前缀最终完成所需的所有执行；如果前缀没有按时完成，窗口之后当然可能还有执行参与最终完成。证书只刻画“为什么该前缀不能在当前局部时间界内闭合”所需覆盖的处理器和能量需求。因此，证书中的能量项 $E_k^\Theta(\omega)$ 应理解为消除该局部阻塞所需支付的保守能量需求上界，而不是对窗口内已完成执行的逐项记录。

## 10.1 局部窗口包含引理（Local Window Containment Lemma）

**引理。** 固定参考前缀 $x$ 和候选局部偏移 $\delta$。若要判断目标作业释放后纯算力参考前缀 $x$ 是否能在半开真实时间区间

$$
[r_k,\ r_k+x+\delta)
$$

内完成，则任何会导致该前缀在该时间界内失败的处理器竞争、并发能耗、额外先行执行或充能等待，都必须能由该局部窗口内的前缀级阻塞/需求证书覆盖。释放时间不早于 $r_k+x+\delta$ 的作业，以及只在该时间界之后执行的作业，不能作为该前缀未能在 $x+\delta$ 内完成的原因。

这里的“需求证书”不是窗口内已经完成执行的逐项记录。若参考前缀没有按时完成，窗口之后当然可能还有执行参与最终完成；这些未来执行不能解释“为什么该前缀没有在当前时间界内完成”。证书只记录在局部时间界之前已经释放、已经滞留、已经占用处理器、已经造成能量消耗，或者已经足以形成能量等待的因果相关需求。

**证明。** 参考前缀 $x$ 的局部判定只关心半开区间

$$
[r_k,\ r_k+x+\delta)
$$

结束之前目标参考进度是否已经推进到 $x$。任何在该区间之后才释放的作业，在区间内不可执行；任何虽然已经释放但只在区间之后执行的作业，也没有在该区间内占用处理器或消耗能量。因此，它们不能解释该前缀为什么没有在时间界 $r_k+x+\delta$ 前完成。

局部窗口内需要覆盖的是已经具有因果影响的需求：高优先级作业可能占用处理器，或者在目标没有推进时先行执行；低优先级作业只能在目标执行阶段并发消耗能量；能量等待只可能由该局部时间界之前需要支付的执行需求触发。于是，用长度 $x+\delta$ 的工作量上界 $W_i^\Theta(x+\delta)$ 和 $W_j^D(x+\delta)$ 覆盖这些局部相关需求是安全的。该引理并不声称窗口之后的未来执行不存在，只说明它们不能作为当前局部失败的原因。

## 10.2 局部需求证书的工作量上界安全性

考虑参考前缀 $x$ 在局部时间界 $x+\delta$ 内是否闭合的问题。由局部窗口包含引理，任何能够解释该前缀未闭合的高优先级相关需求，都必须来自长度 $x+\delta$ 的局部窗口，或者来自该局部窗口起点之前尚未完成、但在 first-counterexample 语境中可由 certified carry-in 参数覆盖的旧作业。

对已认证高优先级任务 $\tau_i\in hp(k)$，在任务集级 first-counterexample 语境中，其 carry-in 滞留由

$$
\Theta_i=R_i^{\Theta,\mathrm{loc},UB}\le D_i
$$

安全覆盖。因此，任何能在局部时间界之前参与处理器竞争、额外先行执行或能量等待的 $\tau_i$ 相关需求，都属于长度 $x+\delta$ 的 certified carry-in workload 影响范围。即使其中某些需求在该时间界内只是 pending，而不是已经完整执行，它们也必须来自局部窗口内释放的作业或由 $\Theta_i$ 覆盖的 carry-in 作业，不能来自窗口之后的未来释放。因此局部证书中 $\tau_i$ 能贡献的总需求量不超过

$$
W_i^\Theta(x+\delta).
$$

同时，$\tau_i$ 是顺序任务，在长度为 $x+\delta$ 的局部窗口内最多能对该局部证书贡献 $x+\delta$ 个单位的执行/需求容量。因此证书变量满足

$$
a_i+b_i+u_i
\le
\min\{W_i^\Theta(x+\delta),x+\delta\}.
$$

对低优先级任务 $\tau_j\in lp(k)$，本文不使用其响应时间上界，而保守使用 deadline-parameterized 工作量上界。局部证书中的低优先级需求只能作为目标执行阶段的并发能耗出现；它不能解释目标的纯算力等待，也不能绕过能量阻塞。因此

$$
c_j\le W_j^D(x+\delta).
$$

这条低优先级约束不是孤立单任务语境下的无条件结论；它依赖第 10.6 节的任务集级 first-counterexample 证明。在全系统第一个反例之前，不存在已经到达绝对截止期却仍未完成的低优先级旧作业，因此低优先级 backlog 不会任意积压，$W_j^D(x+\delta)$ 可以覆盖局部窗口内与目标并发的低优先级需求。

## 10.3 局部容量约束安全性

参考进度 $x$ 中，若目标作业已经执行 $z$ 个参考单位，则纯算力等待长度为 $x-z$。这 $x-z$ 个参考等待单位由高优先级执行填满全部 $M$ 个处理器，因此

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

同时，顺序任务约束给出

$$
b_i\le z,\qquad c_j\le z.
$$

最后，$u_i$ 只统计未被参考进度 $x$ 吸收的高优先级额外先行需求。局部时间界长度为 $x+\delta$，其中最多 $x$ 个单位被纯算力参考进度吸收，剩余用于容纳未计入参考进度的额外时间不超过 $\delta$。每个单位时间最多容纳 $M$ 个处理器执行单位，因此

$$
\sum_{\tau_i\in hp(k)}u_i\le M\delta.
$$

该约束只排除局部处理器容量无法容纳的变量组，不改变

$$
U(\omega)=\sum_i u_i
$$

作为保守时间偏移的解释。

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
x+\delta
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

因此，长度 $x+\delta$ 的真实时间区间同时足以覆盖纯算力参考进度 $x$、未被参考进度计入的高优先级额外先行需求 $U(\omega)$，以及支付局部证书所需的保守能量需求 $E_k^\Theta(\omega)$。在 A6 的在线能量服务曲线语义和 A8 的单位时间可执行性条件下，该局部闭合条件排除了由处理器额外偏移或能量等待导致的超时。

## 10.5 真实失败证书投影引理与局部转换引理

本节把两个逻辑步骤分开。第一步说明：若参考前缀 $x$ 真的不能在 $x+\delta$ 内完成，则导致该失败的真实前缀级阻塞/需求证书可以投影进局部变量集合。第二步说明：一旦该真实失败证书被局部最大化覆盖，并且 $G_k^{\Theta,\mathrm{loc}}(w,x,\delta)\le\delta$，则该失败不可能发生。

### 10.5.1 真实失败证书投影引理

**引理。** 在第 10.6 节的 taskset-level first-counterexample 语境中，对固定 $w$、$x$ 和 $\delta$，若参考前缀 $x$ 不能在半开区间

$$
[r_k,\ r_k+x+\delta)
$$

内完成，则存在一个前缀级阻塞/需求证书

$$
\omega^\star=(z,a_i,b_i,u_i,c_j)
$$

满足

$$
\omega^\star\in\Omega_k^{\Theta,\mathrm{loc}}(w,x,\delta).
$$

**证明。** 令

$$
I=[r_k,\ r_k+x+\delta)
$$

为局部半开窗口。由局部窗口包含引理，窗口 $I$ 之后释放或执行的作业不能作为“参考前缀 $x$ 未能在 $x+\delta$ 内完成”的原因。因此，若该前缀在该时间界内失败，失败原因必须能由 $I$ 内已经形成因果影响的处理器竞争、额外先行执行、并发能耗和能量等待归纳成一个前缀级阻塞/需求证书。

该证书不是完整完成历史。若参考前缀尚未完成，某些未来执行可能在 $I$ 之后才发生；这些未来执行不会被计入当前证书。当前证书只统计在局部时间界前已经释放、已经滞留、已经造成竞争或已经足以形成能量等待的需求。

首先，令 $z$ 为参考前缀 $x$ 中目标作业的参考执行量。由纯算力参考进度定义，

$$
z\in Z_k^\Theta(w,x).
$$

其次，参考前缀中未由目标执行贡献的部分长度为 $x-z$。这些参考等待单位在纯算力解释中由高优先级处理器需求填满。把属于任务 $\tau_i$ 的部分记为 $a_i$，则

$$
\sum_{\tau_i\in hp(k)}a_i=M(x-z),
$$

并由顺序任务约束有

$$
a_i\le x-z.
$$

再次，在目标参考执行阶段，与目标并发并影响能量账的高优先级需求计入 $b_i$，低优先级并发需求计入 $c_j$。目标执行阶段只有 $z$ 个单位，且目标自身占用一个核心，因此

$$
b_i\le z,
\qquad
c_j\le z,
$$

以及

$$
\sum_{\tau_i\in hp(k)}b_i+
\sum_{\tau_j\in lp(k)}c_j
\le (M-1)z.
$$

最后，所有未被参考进度 $x$ 吸收、但会优先于目标参考推进并造成额外处理器等待或能量等待的高优先级需求，计入 $u_i$。这些需求只能放入局部窗口中相对参考进度的额外部分，该部分长度不超过 $\delta$，每个单位时间最多容纳 $M$ 个处理器执行单位，因此

$$
\sum_{\tau_i\in hp(k)}u_i\le M\delta.
$$

由第 10.2 节的局部需求证书工作量上界，所有计入 $a_i+b_i+u_i$ 的高优先级需求都由局部窗口工作量上界覆盖：

$$
a_i+b_i+u_i
\le
\min\{W_i^\Theta(x+\delta),x+\delta\}.
$$

同理，所有计入 $c_j$ 的低优先级并发需求由

$$
c_j\le W_j^D(x+\delta)
$$

覆盖。于是，任何能够解释参考前缀 $x$ 未能在 $x+\delta$ 内完成的真实局部失败，都对应至少一个

$$
\omega^\star\in\Omega_k^{\Theta,\mathrm{loc}}(w,x,\delta).
$$

证毕。

### 10.5.2 局部闭合转换引理

**引理。** 在第 10.6 节的 taskset-level first-counterexample 语境中，对固定 $w$、$x$ 和 $\delta$，如果任意可能导致参考前缀 $x$ 不能在 $x+\delta$ 内完成的真实前缀级证书都可投影为某个

$$
\omega\in\Omega_k^{\Theta,\mathrm{loc}}(w,x,\delta),
$$

并且

$$
G_k^{\Theta,\mathrm{loc}}(w,x,\delta)
\le\delta,
$$

则实际 ASAP-BLOCK 调度完成纯算力参考进度长度 $x$ 的时间不超过 $x+\delta$。

**证明。** 反设参考前缀 $x$ 不能在真实时间 $x+\delta$ 内完成。由 10.5.1 的投影引理，存在一个真实失败证书

$$
\omega^\star\in\Omega_k^{\Theta,\mathrm{loc}}(w,x,\delta).
$$

由于

$$
G_k^{\Theta,\mathrm{loc}}(w,x,\delta)
\le\delta,
$$

而 $G$ 对 $\Omega_k^{\Theta,\mathrm{loc}}(w,x,\delta)$ 中所有变量组取最大值，所以对该真实失败证书 $\omega^\star$ 必有

$$
\delta\ge U(\omega^\star)
$$

以及

$$
E_0+\beta_l(x+\delta)
\ge E_k^\Theta(\omega^\star).
$$

第一条不等式说明局部偏移 $\delta$ 已经覆盖所有未被参考进度吸收的高优先级额外需求。第二条不等式结合 A6 说明，在长度 $x+\delta$ 的真实区间内，窗口初始能量与在线收集能量足以按时间顺序支付该证书代表的保守能量需求；结合 A8，单位时间内被 ASAP-BLOCK 选入执行的前缀也具备可支付性。

因此，任何能够被投影进局部变量集合的真实失败证书，其处理器需求、额外先行需求和能量需求都不能造成超过 $\delta$ 的额外延迟。这与 $\omega^\star$ 是阻止参考前缀 $x$ 在 $x+\delta$ 内闭合的真实失败证书矛盾。故参考前缀 $x$ 必能在真实时间 $x+\delta$ 内完成。引理成立。

## 10.6 任务集级主定理（first-counterexample 形式）

前面各节给出的局部前缀引理本身只说明：在给定的局部窗口、工作量上界和容量约束都安全覆盖真实阻塞/需求证书时，闭合偏移能够保证对应参考前缀完成。为了把这一局部结论提升为完整 RTA 的安全性，主定理必须放在 taskset-level first-counterexample 语境中。特别是，局部变量集合仍然包含低优先级并发项

$$
c_j\le W_j^D(x+\delta),\qquad \tau_j\in lp(k),
$$

这条约束的安全性依赖于：在被分析的反例窗口之前，低优先级任务不存在任意旧 backlog。该性质由全系统第一个反例保证，而不是由孤立单任务分析自动保证。

**定理（任务集级充分性）。** 设任务集按固定优先级从高到低递推计算局部窗口 RTA。对每个任务 $\tau_k$，若在所有高优先级任务已经认证的前提下，存在

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

安全覆盖。因此，局部窗口中对高优先级任务使用

$$
W_i^\Theta(x+\delta)
$$

是安全的。

其次，对任意低优先级任务 $\tau_j\in lp(k)$，本文并不使用其 certified response-time carry-in，而是使用 deadline-parameterized bound

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

**说明。** 如果脱离 taskset-level first-counterexample 语境，仅想声明一个孤立单任务上界，则不能无条件保留低优先级约束 $c_j\le W_j^D(x+\delta)$，因为低优先级任务可能携带任意旧 backlog。本文选择保留该有用的局部工作量约束，并将主正确性结论表述为任务集级充分性定理。

# 11. 局部窗口分析与完整窗口基线的连接关系

局部窗口分析应当被理解为完整窗口分析的一种局部窗口紧化尝试：它保留 certified carry-in、处理器容量耦合和额外先行执行时间账，只把能量偏移项中用于工作量枚举的窗口从完整候选窗口 $w$ 缩小到针对参考前缀 $x$ 的局部窗口 $x+\delta$。这个关系是固定输入证书层面的变量域比较，不是任务集级递推结果的无条件非劣定理。

需要强调的是，下面的包含关系只在**固定同一组输入证书**时成立。也就是说，比较两种分析时必须固定相同的任务参数、相同的 $E_0$、相同的 $\beta_l$，并且固定相同的 certified carry-in 参数 $\Theta_i$。如果两种分析分别按自己的递推链得到不同的 $\Theta_i$，那么 $W_i^\Theta(\cdot)$ 本身已经不同，不能直接从局部窗口包含关系推出任务集级结果一定非劣。

令完整窗口分析的能量偏移为

$$
B_k^{E,\Theta}(w),
$$

局部窗口分析的能量偏移为

$$
B_k^{E,\Theta,\mathrm{loc}}(w).
$$

假设某个候选窗口 $w$ 在完整窗口分析下已经闭合：

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

同时，完整窗口闭合证明给出：在闭合候选窗口下，能够形成前缀级反证障碍的变量组满足

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

且局部可行集合非空时，局部窗口 $x+\delta$ 不大于完整窗口 $w$，低优先级和高优先级工作量上界均不更大，局部容量约束

$$
\sum_i u_i\le M\delta
$$

也与完整窗口闭合证明中的额外偏移容量一致。在这些前提下，局部窗口分析对同一参考前缀的最大化域不大于完整窗口分析的对应最大化域，因此完整窗口偏移可以作为局部内层闭合搜索的候选证书。

于是只能得到以下带前提、固定输入证书层面的包含关系：

$$
\boxed{
\text{在相同 }\Theta_i,E_0,\beta_l\text{ 和任务参数下，若 }\delta=B_k^{E,\Theta}(w)
\text{ 对每个 }x\text{ 都给出非空局部闭合候选，则 }
B_k^{E,\Theta,\mathrm{loc}}(w)\le B_k^{E,\Theta}(w).
}
$$

进一步，在上述同一前提下，才可得到

$$
F_k^{\Theta,\mathrm{loc}}(w)
\le
F_k^\Theta(w).
$$

该不等式不应被外推为“局部窗口分析在完整任务集递推中总是不差于完整窗口分析”。这个关系不能被解释为“任务集级递推结果无条件非劣”。原因至少有三点。第一，局部分析和完整窗口分析若各自递推，可能得到不同的 certified carry-in 参数。第二，局部分析对空集合采取保守处理，过小的局部窗口不会被错误地视为已经闭合。第三，工程实现还可能出现 timeout、搜索上界或 fallback 策略带来的保守性。因此，实验中仍应保留完整窗口基线，并用一致性检查记录局部窗口结果是否出现更保守的情况。

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

所以这些中间值均不满足 $G(\delta')\le\delta'$，实现可以安全地直接跳到 $g$。若 $g=\infty$，或者 $g$ 已经大于当前候选窗口允许的搜索上界 $\delta_{cap}$，则当前候选 $w$ 可以直接判定为不可证明，不能继续枚举到无界范围，也不能把该结果当作闭合证书。结合第 8.1 节的有限局部闭合搜索引理，内层搜索不假定闭合一定存在；若在有限上界内找不到闭合证书，则安全返回当前候选 $w$ 不可证明。伪代码如下：

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

        if g == infinity or g > delta_cap:
            return no closure within current candidate w

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

完整窗口形式可以自然写成外层固定点迭代；本文由于加入内部 $\delta$ 闭合与空集处理，工程实现上使用候选 $w$ 扫描更稳妥。若实现采用固定点迭代，也必须在每个候选 $w$ 上重新验证闭合条件

$$
F_k^{\Theta,\mathrm{loc}}(w)
\le w.
$$
