# 结构化目录

`catalog/` 为知识库提供稳定、可机器读取的索引。

- [`articles.json`](articles.json)：已收录文章、摘要、图片和分类标签。
- [`article-tags.json`](article-tags.json)：每篇文章终审后的显式分类记录；缺少时停止收录，不用关键词自动猜测。
- [`taxonomy.json`](taxonomy.json)：稳定标签 ID 与中文名称。
- [`assets-manifest.json`](assets-manifest.json)：公开成品的文件大小与 SHA-256，用于完整性核验。

这些文件只描述公开内容，不包含未发布题库、内部运行状态、个人信息或本机路径。
