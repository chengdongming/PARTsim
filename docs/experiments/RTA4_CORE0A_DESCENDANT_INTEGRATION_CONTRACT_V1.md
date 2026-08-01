# RTA4 CORE-0A 后继集成合约 V1

## 目的与边界

`REVIEWED_DESCENDANT_INTEGRATION_MERGE_V1` 在既有 CORE-0A repository-lineage
身份内认证少量、逐个审阅且可由 Git 完整重建的后继二父合并。它没有建立平行
lineage identity，也没有把“共享 anchor”或“来自项目分支”当作授权条件。

验证器从洁净工作树的 `HEAD` 沿 first-parent 链回溯。普通一父后继继续使用原有
规则；遇到二父合并时，只有 commit SHA 精确存在于版本化 registry、顺序连续且
全部登记字段与 Git 重建结果完全一致时才继续。未登记合并、跳过的中间合并、
八爪合并、父顺序交换、ancestor 替换、tree/path/blob/classification 篡改均 fail
closed。分支名、remote、提交消息和调用者提供的路径不参与信任判断。

V1 只认证两侧 changed-path 集合不相交的合并。重建过程固定使用 ordered parents
及唯一 merge-base，分别计算原始 no-renames path 集合与启用 rename/copy 检测的
状态记录，校验 merge 相对任一 parent 的差异恰好来自另一侧，并以 base tree 加
两侧 blob/deletion 状态重建完整结果 tree。copy/rename 还会通过旧路径是否保留在
结果 tree 中再次分类，防止仅凭相似度误判。

## 身份闭包

Registry 使用严格 UTF-8 canonical JSON：键排序、无多余空白、单个结尾换行、
拒绝重复键和额外字段。每个合约绑定：

- schema/version、merge 类型、序号和 ordered parent；
- merge commit、parent/base/result tree；
- 两侧及相对两 parent 的 path-set SHA、status-record SHA 和
  ADD/MODIFY/DELETE/COPY/RENAME 计数；
- overlap path-set SHA、结果 touched-blob-state SHA 和完整重建 tree；
- predecessor reviewed integration anchor、前置 effective identity 和合约内容 SHA。

合约内容 SHA 按顺序形成 effective-lineage 链。Registry 内容 SHA、ordered contract
SHA 列表、最终 effective identity、当前 `HEAD` 和当前 tree 一起进入原有
`repository_lineage_identity`。旧的无后继合约 topology 不增加这些字段，因此原有
V1 身份材料保持不变。Registry 本身位于 production source closure 中，prepared
材料会绑定其文件内容；这避免了身份循环，因为登记合约只引用已经存在的两个
merge 及其 predecessor identity，不引用随后提交生成的最终 repository identity。

## 当前登记链

Registry：
`experiments/v9_3/rta4_core0a_descendant_integration_contracts_v1.json`

- registry content SHA-256：
  `c1c24709419fcf6738213648abe0635ac4b671548f2f6de294ea1447322fa93c`
- predecessor reviewed integration anchor：
  `4a04e2afd88424b8ebe85500b0561d7203c64e4e`

登记合并 1：

- commit：`8ea8f209f274bd329e41cb0b1ab59265983b3631`
- ordered parents：`4a04e2afd88424b8ebe85500b0561d7203c64e4e`，
  `d0a37d67f913c44252791316d1140034f04cf285`
- result/reconstructed tree：`e728475571031724606f8729204b6055034307a2`
- 两侧 path 数：0 / 2；第二侧分类：2 MODIFY；overlap：0
- contract content SHA-256：
  `4a56001e55ebbc6f35a0e69905295b503a7ec06d2d682a9739ba9069f56fa7d3`

登记合并 2：

- commit：`95b9045612cfa908aaecea6ae3440d2bd9a0d6ec`
- ordered parents：`8ea8f209f274bd329e41cb0b1ab59265983b3631`，
  `af8a092121087e25dc080de82e6f9194a0d1e0a6`
- result/reconstructed tree：`cdbf5122396a7226f1dbde981b80b5016649d7d2`
- 两侧 path 数：2 / 52；第二侧分类：42 ADD、9 MODIFY、1 COPY；
  overlap：0
- contract content SHA-256：
  `b553a0ff2716eda165e367d252906e2b23de0d4531eb72e3bcff3fd399b42fe9`

该登记只认证以上精确对象和顺序，不授权后续未知 merge，不改变 Stage A/A.5
结果、RTA 数学或 `formal_t10_campaign_authorized=false` 的状态。
