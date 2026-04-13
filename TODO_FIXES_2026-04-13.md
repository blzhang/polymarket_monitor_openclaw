# Polymarket Monitor 修复清单（2026-04-13）

## P1
- [ ] 去掉 websocket 主循环中的同步 HTTP 拉取，避免事件循环阻塞
- [ ] watchlist 热更新后触发订阅集变更（重连或 subscribe/unsubscribe）

## P2
- [ ] 合并/收口为单一实现，避免 `poll_state_http.json` 与 `poll_state.json` 双状态漂移
- [ ] `CRON_SETUP.md` 与真实运行路径对齐
- [ ] HTTP/WS 路径的 expiresAt 过滤逻辑保持一致

## P3
- [ ] 增加最小测试：YES/NO 归一化、5 分钟窗口成交量、watchlist 订阅变更、broadcast 状态契约
- [ ] 更新过期 TOOLS.md
