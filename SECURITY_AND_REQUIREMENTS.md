# GitHub Discovery - 安全性和需求说明

## 📋 你需要提供什么

### 1. GitHub Personal Access Token（必需）

**用途**：
- 调用 GitHub API 搜索仓库
- 读取仓库信息（README、代码等）
- 不会修改任何内容，只读取

**如何创建**：
1. 访问 https://github.com/settings/tokens/new
2. 选择 "Generate new token (classic)"
3. 设置名称：`AI Digest - GitHub Discovery`
4. **只勾选以下权限**（最小权限原则）：
   - ✅ `public_repo` (只读公开仓库)
   - ❌ 不要勾选其他任何权限！

**风险**：
- ✅ **低风险** - 只能读取公开信息
- ✅ Token 只用于读取，不会修改/删除任何内容
- ✅ 可以随时撤销（https://github.com/settings/tokens）

### 2. Notion Integration Token（可选）

**用途**：
- 仅当你想自动同步到 Notion 时需要
- 创建新页面展示发现的项目

**如何创建**：
1. 访问 https://www.notion.so/my-integrations
2. 创建新 Integration
3. 授权访问特定 Database

**风险**：
- ✅ **低风险** - 只能访问你授权的 Database
- ✅ 只创建新页面，不修改现有内容
- ✅ 可以随时删除 Integration

---

## 🔒 安全措施

### Token 存储

**配置文件位置**：
```
config/github_config.json  （本地文件，不会上传到 GitHub）
config/notion_config.json   （本地文件，不会上传到 GitHub）
```

**已添加到 .gitignore**：
```
config/github_config.json
config/notion_config.json
```

✅ 你的 token 永远不会被提交到 Git 仓库

### 权限范围

| Token | 能做什么 | 不能做什么 |
|-------|----------|-----------|
| GitHub Token | ✅ 读取公开仓库<br>✅ 搜索仓库<br>✅ 读取 README | ❌ 修改代码<br>❌ 删除仓库<br>❌ 访问私有仓库<br>❌ 修改设置 |
| Notion Token | ✅ 在指定 Database 创建页面 | ❌ 修改现有页面<br>❌ 删除页面<br>❌ 访问其他 Database |

---

## 💰 成本风险

### GitHub API

**免费额度**：
- 5,000 次请求/小时（authenticated）
- 完全免费，不会产生费用

**系统使用量**：
- 每次运行约 100-200 次 API 调用
- 远低于免费额度

✅ **无成本风险**

### Claude API

**费用**：
- 使用你现有的 Claude API 配置
- 每次运行约 $15-20
- 月度约 $150-200（每 3 天运行一次）

**成本控制**：
```json
{
  "target_count": 10,    // 减少发现数量
  "model": "haiku"       // 使用更便宜的模型
}
```

✅ **成本可控且透明**（每次运行后显示实际成本）

### Notion API

**免费额度**：
- 完全免费（个人使用）

✅ **无成本风险**

---

## 🛡️ 风险分析

### 高风险 ❌（本系统不涉及）

- ❌ 修改/删除你的 GitHub 仓库
- ❌ 访问私有仓库
- ❌ 发布/推送代码
- ❌ 修改权限设置
- ❌ 访问敏感信息

### 低风险 ⚠️（可控）

- ⚠️ **Token 泄露**
  - **缓解措施**：Token 只存储在本地
  - **紧急措施**：立即在 GitHub 撤销 token

- ⚠️ **成本超支**
  - **缓解措施**：每次运行显示成本
  - **紧急措施**：调整配置或停止运行

- ⚠️ **API 限流**
  - **缓解措施**：系统自动处理限流
  - **影响**：最多延迟 1 小时（等待限流重置）

### 无风险 ✅

- ✅ 只读取公开信息
- ✅ 不修改任何内容
- ✅ 可随时停止
- ✅ 可随时撤销 token

---

## 🔐 最佳安全实践

### 1. 使用最小权限

**GitHub Token**：
```
只勾选 public_repo
不要给予写入权限
```

### 2. 定期轮换 Token

```bash
# 每 3-6 个月更换一次 token
# 1. 在 GitHub 创建新 token
# 2. 更新 config/github_config.json
# 3. 撤销旧 token
```

### 3. 监控使用情况

```bash
# 检查 API 使用量
curl -H "Authorization: token YOUR_TOKEN" \
  https://api.github.com/rate_limit

# 查看系统日志
tail -f logs/github_discovery.log
```

### 4. 不要共享配置文件

```bash
# 确保不会意外提交
git status
# config/github_config.json 应该显示为 untracked
```

---

## 🚨 紧急响应

### 如果 Token 泄露

1. **立即撤销**：
   - GitHub: https://github.com/settings/tokens
   - Notion: https://www.notion.so/my-integrations

2. **创建新 Token**：
   - 使用相同的最小权限

3. **更新配置**：
   ```bash
   nano config/github_config.json
   ```

4. **检查异常活动**：
   - GitHub: Settings → Security log
   - Notion: Settings → Activity

### 如果成本过高

1. **立即停止调度器**：
   ```bash
   # 找到调度器进程
   ps aux | grep run_scheduler
   # 终止进程
   kill <PID>
   ```

2. **调整配置**：
   ```json
   {
     "target_count": 5,      // 减少数量
     "model": "haiku",       // 使用便宜模型
     "max_stars": 500        // 缩小范围
   }
   ```

3. **禁用自动运行**：
   - 只手动运行，不启动调度器

---

## ✅ 安全检查清单

在首次运行前，确认：

- [ ] GitHub Token 只有 `public_repo` 权限
- [ ] config/*.json 文件已添加到 .gitignore
- [ ] 了解每次运行的预期成本（~$20）
- [ ] 知道如何撤销 token（GitHub Settings）
- [ ] 保存了紧急响应步骤

---

## 📞 常见问题

### Q: Token 会被上传到 GitHub 吗？

**A**: 不会。`config/*.json` 已添加到 `.gitignore`，Git 会忽略这些文件。

### Q: 系统会修改我的 GitHub 仓库吗？

**A**: 不会。系统只读取公开信息，token 权限也只有读取权限。

### Q: 如果忘记撤销 token 会怎样？

**A**: Token 权限很有限，只能读取公开仓库。但建议定期检查并撤销不用的 token。

### Q: 成本会失控吗？

**A**: 不会。每次运行都显示实际成本，你可以随时停止。GitHub API 完全免费。

### Q: 可以在公司网络使用吗？

**A**: 可以，但请遵守公司的 API 使用政策。Token 权限很有限，只读取公开信息。

---

## 📊 权限对比

| 操作 | 需要的权限 | 本系统使用 | 安全性 |
|------|-----------|-----------|--------|
| 读取公开仓库 | `public_repo` | ✅ | ✅ 安全 |
| 修改代码 | `repo` (write) | ❌ 不使用 | ✅ 安全 |
| 删除仓库 | `delete_repo` | ❌ 不使用 | ✅ 安全 |
| 访问私有仓库 | `repo` (full) | ❌ 不使用 | ✅ 安全 |
| 修改设置 | `admin` | ❌ 不使用 | ✅ 安全 |

---

## 🎯 总结

### 需要提供

1. **GitHub Token**（必需，5 分钟创建）
   - 只需 `public_repo` 权限
   - 只读取公开信息

2. **Notion Token**（可选）
   - 仅用于自动同步
   - 可以不配置

### 风险评估

- **安全风险**：✅ 极低（只读权限）
- **隐私风险**：✅ 无（只访问公开信息）
- **成本风险**：✅ 可控（~$20/次，透明显示）

### 推荐做法

1. 使用最小权限创建 token
2. 先手动运行一次测试
3. 检查成本后再启用自动运行
4. 定期检查 token 使用情况

---

**结论**：✅ 安全且可控，风险极低
