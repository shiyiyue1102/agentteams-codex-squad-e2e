# AgentTeams development squad demo

This isolated repository validates a confirmation-first development squad.

Serve this directory with `python3 -m http.server 8000` and open the page. The counter starts at zero and its +1 button should increase the displayed value by exactly one per click.

Run checks with `npm test`. Contributions should keep this example dependency-free, include a focused regression check and reference the assigned Issue. Never merge a PR automatically.

## Issue #13：AgentTeams 独立中文首页

直接双击 `agentteams-home.html`，或用浏览器打开该文件的 `file://` 地址。
请保留旁边完整的 `assets/agentteams/` 目录；无需安装依赖、构建或启动服务器。
既有 `index.html` 计数器保持不变。

页面按 2026-11-26 的 https://agentteams.cloud/ 快照还原，包含导航、首屏五图预览、
快速开始、六种运行时、协作流程、Dashboard、架构图、收尾 CTA 和页脚。
本页可视资源全部本地化；文档、社区、源码、商业版等外链仍需联网。
点击预览分页或用左右键切换，鼠标悬停可放大，Esc 关闭；减少动态效果时禁用自动轮播。
复制按钮只复制文本，不执行安装命令；剪贴板不可用时会选中命令并提示手动复制。
没有 JavaScript 时正文和首张产品图仍然可读。

运行 `npm test` 执行零依赖静态回归检查。浏览器自测记录与桌面/移动截图随任务交付，
不将测试浏览器或其依赖加入本项目。素材来源与使用边界见 `assets/agentteams/SOURCES.md`。
