# 提示词：从文献 PDF 中扩充 DFT+U 的 U 值库

> 用途：把一批新的文献 PDF 交给 coding agent，自动筛选出"使用 VASP + DFT+U"的工作，
> 抽取元素 / Ueff / U / J / DOI，作为**补充**合并进 `References-out/` 数据库与 `add-u.py`。
> 复制下面整段内容作为提示词即可。

---

## 角色

你是"第一性原理文献挖掘 + 代码维护"助手。目标：从给定目录的 PDF 文献中，找出**实际使用了
VASP 程序包并施加了 DFT+U** 的工作，抽取 U 值参数，整理成数据库，并以**只增不改**的方式
扩充 `add-u.py` 中的 U 值库。

## 输入

- 文献目录：`References/`（或用户指定目录）
- 输出目录：`References-out/`
- 目标脚本：`add-u.py`
- 已有数据库：`References-out/dft_u_database.csv`（若存在，作为已有记录，不得删除）

## 任务

1. **逐个**读取目录下每个 PDF 的全文（可用 `pymupdf` 或 `pdftotext`）。
2. 判断该文献是否**同时满足**：
   - ① 使用 VASP（"Vienna ab initio Simulation Package" / "VASP"）；
   - ② 实际应用了 DFT+U（如 `DFT+U`、`DFT + U`、`GGA+U`、`PBE+U`、`RPBE+U`、`LSDA+U`、
     `SCAN+U`，或明确写出 Hubbard U、LDAUU/LDAUJ、on-site Coulomb U 的设置）。
3. 对命中文献抽取参数并以句子为单位给出证据（便于复核）。
4. 将命中的 PDF（含主文与对应 SI）复制到 `References-out/`。
5. 生成/更新 CSV 数据库，并把新记录合并进 `add-u.py` 的 `U` 表。
6. 运行自检（见末尾）。

## 判断规则（关键）

- **必须**是 VASP。使用其他程序包的**排除**：Quantum ESPRESSO/PWscf、PWmat、GPAW、CRYSTAL、
  JDFTx、CASTEP、SIESTA、Gaussian、DMol3、ABINIT、Wien2k、FHI-aims 等。
- 仅"引用/讨论"DFT+U（如参考文献标题里出现 "LSDA+U study"）而**未实际使用**的文献排除。
- 排除假阳性：电极电位 `U = x V`、电压、内能变量、`ΔU`(反应选择性) 等，均不是 Hubbard U。
- U 值必须有**具体数值**（元素 + U / J / Ueff）。只有"用了 DFT+U"而无数值的，只作说明、不入库。
- 区分原始量与有效量：VASP（Dudarev，`LDAUTYPE=2`）实际使用 `Ueff = U - J`。
  记录时同时保留 `U`、`J`、`Ueff`；写 INCAR 时 `LDAUU=U`、`LDAUJ=J`。
- p 轨道（O/N/S/Se/F/Cl/Ga/Pb/As 等）的 U 值通常为**可选/说明**，不要默认施加（见 `OPTIONAL`）。

## 提取字段

`元素, 轨道(d/f/p), LDAUL, Ueff(eV), U(eV), J(eV), DOI, title, source_pdf, notes`

- `notes` 注明场景：体系/材料、赝势、U 取值范围、是否仅测试、是否来自 SI 等。

## 输出格式

1. `References-out/`：复制进来的 PDF。
2. `References-out/dft_u_database.csv`，表头固定为：
   `element,orbital,LDAUL,LDAUU_eff_eV,LDAUJ_eV,doi,title,source_pdf,notes`
3. `References-out/references_with_dftu.csv`：
   `source_pdf,doi,title`
4. 更新 `add-u.py` 的 `U` 表：
   - `U` 的记录格式为 **4 元组** `(轨道, U, J, DOI)`；轨道取 `"d"/"f"/"p"`。
   - **已存在元素的第 0 条 = 原默认值，必须原样保留**；新记录**追加**到该元素的列表末尾。
   - 新元素：新增键；若该元素应默认施加 U，则把它加入 `DEFAULT_U`（不改动已有元素的值），
     否则加入 `OPTIONAL`（仅 `-u` 显式选择时才施加）。
   - 不得修改 `parse_poscar` / `build_dftu` / `prompt` / `append_dftu` / `main` 的外部行为、
     CLI 选项、交互表格与 `#References` 功能；保持代码简洁。

## 去重与冲突处理

- 相同 `(元素, U, J, DOI)` 只保留一条。
- 同一 DOI、同一元素但 U 不同：全部保留为候选。
- 同一元素不同文献的 U：全部保留为候选（交互选择时列出）。
- 若同一工作有主文 + SI，两处 U 值一致则合并，`source_pdf` 用 `; ` 连接。
- 结束时输出摘要：新增/更新的元素、Ueff、DOI、来源文件；以及被排除的可疑文件与原因。

## 质量要求

- 不臆造 DOI；查不到 DOI 时用 `title` + `source_pdf`。
- 对 PDF 文件名/封面与正文不一致的情况，以正文实际内容为准，并在 `notes` 标注。
- 判读不确定时在 `notes` 中写明。
- 保持 `add-u.py` 结构简洁（数据表 + 少量短函数 + argparse CLI）。

## 自检清单

```bash
python -m py_compile add-u.py        # 语法
python add-u.py -l                   # 能否列出 U 值库
# 用临时 POSCAR 验证默认值未被修改、交互/选择/引用块正常：
python add-u.py -p _test/POSCAR -d -n
python add-u.py -p _test/POSCAR -d -n -u Co=1
# 确认原 DEFAULT_U 全部原值仍在（逐元素比对）
```
- 确认 `U` 表第 0 条（默认值）与扩充前一致。
- 确认新增记录都能在 `-l` 中看到，且带正确 DOI。

---

### 可选：极简口令版

> 扫描 `References/` 下所有 PDF，找出**用 VASP 且实际施加 DFT+U** 的文献：复制 PDF 到
> `References-out/`，把 `元素/轨道/U/J/Ueff/DOI` 写入 `dft_u_database.csv`，并把新记录
> **追加**到 `add-u.py` 的 `U` 表（4 元组 `(轨道, U, J, DOI)`）。已有默认值只增不改；
> 新元素按需加入 `DEFAULT_U` 或 `OPTIONAL`。最后 `py_compile` 并 `-l` 自检，报告新增摘要。
