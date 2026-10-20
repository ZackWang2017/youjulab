---
name: youjulab-announcement-pdf
description: Generate a structured outline for a native-text PDF announcement in youjulab.
---

# 公告 PDF 大纲

使用 generate_pdf_outline(document_id) 为当前用户有权限访问的原生文本 PDF 生成标题结构。不要用于扫描件、图片型 PDF、OCR、摘要生成或表格数值抽取。

工具会复用该 PDF 的向量索引与版式块；模型仅根据受限的版式画像和代表性样本选择已配置的规则包，随后由本地规则解析全文结构。规则位于 `apps/office-backend/announcement_intelligence/rule_packs.json`，覆盖中英文编号、多级编号、单行粗体和字号标题，以及页眉页脚、正文句、表格行等排除条件；修改规则文件后下一次生成即生效。不会让模型读取全文、评估命中率、返回正则或生成补充规则。

status 为 unsupported 时，向用户说明“文档结构复杂，暂时无法生成”，不要根据不可靠候选自行补全标题。不会改变 PDF 正文或笔记。
