# AgentTeams development squad demo

This isolated repository validates a confirmation-first development squad.

Serve this directory with `python3 -m http.server 8000` and open the page. The counter starts at zero and its +1 button should increase the displayed value by exactly one per click.

Run checks with `npm test`. Contributions should keep this example dependency-free, include a focused regression check and reference the assigned Issue. Never merge a PR automatically.

## 阿里云产品选型演示（Issue #12）

直接用浏览器打开 `aliyun-product-guide.html` 即可查看 ECS、OSS 和 RDS 的职责与适用场景，使用页内导航跳转。页面自包含，无需服务器、构建或网络依赖；手机宽度下自动改为单列。此页为非官方演示，独立于首页需求 #11。

运行 `npm test` 包含本页聚焦回归检查。浏览器验收应另行使用本地 `file://` 页面，在桌面与手机视口下逐项检查导航、内容和横向溢出。
