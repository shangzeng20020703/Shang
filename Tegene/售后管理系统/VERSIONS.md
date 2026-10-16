# 售后打卡系统源码版本索引

目录根部为当前源码副本，来源是私有仓库 `Jaydon-Lidong/aftersales-field-management` 的指定提交。`versions/` 保存已固定版本的完整 Git 跟踪源码；每个版本的逐文件 SHA-256、文件数、来源提交和 Git 树号见对应清单。时间编号采用北京时间。

| 版本 | 来源提交 | 逐文件清单 | 状态 |
|---|---|---|---|
| [20261016-180101-CST-87dc66b](versions/20261016-180101-CST-87dc66b/) | `87dc66b9e886d291df1bbcec442d06677e8e22b5` | [JSON](versions/manifests/20261016-180101-CST-87dc66b.json) | 当前；待配置人员、项目规则与每周个人排班；本机预览版，未部署服务器 |
| [20261016-150240-CST-d60ebcb](versions/20261016-150240-CST-d60ebcb/) | `d60ebcb204f22079d8cae0d3ae2130eefdc8c65b` | [JSON](versions/manifests/20261016-150240-CST-d60ebcb.json) | 历史版本；现场权限与跨项目排班审批 |
| [20261016-095945-CST-d753ad1](versions/20261016-095945-CST-d753ad1/) | `d753ad109bbad801425437f9c384ce76a88b29db` | [JSON](versions/manifests/20261016-095945-CST-d753ad1.json) | 历史版本；现场轮休与待休流水 |
| [20261016-095417-CST-e18f737](versions/20261016-095417-CST-e18f737/) | `e18f73794f53be32cbc7870b027e1bb81d3014a2` | [JSON](versions/manifests/20261016-095417-CST-e18f737.json) | 历史版本；现场轮休与待休流水 |
| [20261015-184321-CST-49f8eb8](versions/20261015-184321-CST-49f8eb8/) | `49f8eb8838b8e037a7e8f5184e277e1a837901bf` | [JSON](versions/manifests/20261015-184321-CST-49f8eb8.json) | 历史版本；50 行分页与管理员节假日表 |
| [20261015-142156-CST-5939798](versions/20261015-142156-CST-5939798/) | `59397984c7807a8f57867882c0b0510f8f6ffa7a` | [JSON](versions/manifests/20261015-142156-CST-5939798.json) | 历史版本 |
| [20261009-143452-CST-07d1e7e](versions/20261009-143452-CST-07d1e7e/) | `07d1e7e55800116ce07857873dd30bdff88d58a0` | [JSON](versions/manifests/20261009-143452-CST-07d1e7e.json) | 10 月 9 日基线 |

公开副本中的 `docs/09-账号花名册与企微入口.md` 有两处个人手机号，均已替换为角色占位符。除此之外，各版本目录与对应私有仓库提交的跟踪文件逐字节一致。目录根部的 `.dockerignore` 另外加入 `versions`，避免归档目录进入将来的 Docker 构建上下文。清单记录了这些公开镜像差异；此提交不重写公开仓库既有历史。

今后每次确定新版本，从私有仓库的完整提交制作新编号目录，核对全部跟踪文件并生成校验清单，然后更新目录根部和本索引。数据库、上传附件、部署配置及镜像不属于本公开源码副本，生产发布前另行加密备份和验证恢复。
