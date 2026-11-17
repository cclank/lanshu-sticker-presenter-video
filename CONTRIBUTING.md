# 参与改进

欢迎提交可复现的问题、主题适配案例、布局改进与辅助脚本修复。问题中请注明执行命令、Python/FFmpeg 版本、预期与实际行为，并去掉令牌、私有素材链接和账号信息。

本地检查：

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q scripts assets/reference-project/scripts
bash -n assets/reference-project/scripts/render_all.sh
python3 scripts/check_project.py examples/desk-styling.production.json
```

安装 FFmpeg 后会执行真实媒体与分片渲染检查；缺少它时相关测试会明确跳过。CI 安装 FFmpeg 并执行这些检查，全程不调用付费模型。

提交时说明触发条件、改动后的行为和实际验证结果。参考渲染器的改动请同时更新对应文档；新增素材需注明来源与适用授权，不提交原始密钥、临时签名链接、人物母片或完整项目导出。
