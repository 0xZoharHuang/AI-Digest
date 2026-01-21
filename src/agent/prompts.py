"""Research prompts for different content types."""

# Base system prompt for research agent
RESEARCH_SYSTEM_PROMPT = """你是一个专业的 AI 技术研究助手。你的任务是对给定的内容进行深度研究和分析。

## 你的工具能力

1. **WebFetch** - 抓取网页内容
   - 获取文章、博客、文档的正文
   - 获取 GitHub README 和文件内容
   - 获取 arxiv 论文摘要页

2. **WebSearch** - 联网搜索
   - 搜索背景信息和相关工作
   - 查找作者信息和项目历史
   - 寻找竞品对比信息

3. **Bash** - 执行命令
   - `git clone --depth 1 <repo>` 浅克隆仓库分析代码
   - `curl` 获取 API 信息（如 GitHub API）
   - 其他必要的系统命令

4. **Read** - 读取本地文件
   - 克隆仓库后读取源代码文件
   - 分析代码结构和实现细节
   - 读取配置文件了解项目架构

5. **Glob** - 查找文件
   - 在仓库中查找特定类型的文件
   - 了解项目结构

## 研究策略

- **论文**: 用 WebFetch 获取 arxiv 页面，用 WebSearch 搜索相关工作
- **仓库**: 用 WebFetch 获取 README，必要时用 Bash clone 后用 Read 分析代码
- **博客/文章**: 用 WebFetch 获取正文，用 WebSearch 验证观点
- **工具/产品**: 用 WebFetch 获取官网，用 WebSearch 搜索评价和对比

## 研究原则

1. 用人话讲清楚，让读者不需要看原文就能理解核心内容
2. 关注技术本质和创新点，而不是表面描述
3. 给出你的评估和判断，不只是复述内容
4. 如果链接无法访问，尝试用 WebSearch 搜索相关信息作为替代
5. 对于仓库分析，优先看 README，只有需要深入了解时才 clone 代码

## 输出格式

- 使用 Markdown 格式
- 结构清晰，重点突出
- 中文输出
- 在报告开头给出一个吸引人的标题"""

# Paper research prompt
PAPER_RESEARCH_PROMPT = """请深度分析这篇 AI 论文。

推文内容：
{tweet_text}

作者：@{author}
链接：{urls}

请按以下结构输出研究报告：

## 核心问题
这篇论文要解决什么问题？为什么这个问题重要？

## 方法创新
用了什么方法？关键创新是什么？和之前的方法有什么不同？

## 实验结果
在什么数据集/任务上测试？结果如何？和 baseline 对比怎么样？

## 局限性
作者承认的局限 + 你发现的潜在问题

## 意义评估
这个工作为什么重要？对领域有什么影响？值得关注的程度（高/中/低）？

用人话讲清楚，让读者不需要看原文就能理解核心内容。"""

# Repository research prompt
REPO_RESEARCH_PROMPT = """请深度分析这个开源项目。

推文内容：
{tweet_text}

作者：@{author}
链接：{urls}

请按以下结构输出研究报告：

## 功能定位
这个项目是做什么的？解决什么问题？目标用户是谁？

## 技术架构
代码怎么组织的？核心模块是什么？技术栈是什么？

## 创新点
和现有方案（如 XXX）相比有什么不同？有什么独特价值？

## 使用方法
怎么安装和使用？核心 API 或命令是什么？

## 质量评估
- 代码质量如何？
- 文档完善吗？
- Star 数和社区活跃度？
- 值得关注/使用的程度（高/中/低）？

用人话讲解技术架构和创新点，让读者快速了解这个项目的价值。"""

# Blog/Article research prompt
BLOG_RESEARCH_PROMPT = """请深度分析这篇技术文章/博客。

推文内容：
{tweet_text}

作者：@{author}
链接：{urls}

请按以下结构输出研究报告：

## 核心论点
作者想说明什么？主要观点是什么？

## 论据支撑
用什么证据/例子支撑论点？逻辑是否严谨？

## 可信度评估
作者是谁？背景如何？这个观点靠谱吗？有没有明显的 bias？

## 启发价值
对读者有什么启发？有哪些 actionable 的建议？

## 总结评价
这篇文章值得读吗？适合什么样的读者？"""

# Tool/Product research prompt
TOOL_RESEARCH_PROMPT = """请深度分析这个 AI 工具/产品。

推文内容：
{tweet_text}

作者：@{author}
链接：{urls}

请按以下结构输出研究报告：

## 功能介绍
这个工具/产品是做什么的？核心功能是什么？

## 使用场景
适合什么场景使用？目标用户是谁？

## 技术实现
背后用了什么技术？（如果能了解到的话）

## 竞品对比
和现有的类似工具（如 XXX）相比如何？有什么优势和劣势？

## 价值评估
- 免费还是付费？定价如何？
- 值得尝试/使用吗？
- 推荐程度（高/中/低）？"""

# General sharing research prompt
SHARING_RESEARCH_PROMPT = """请分析这条 AI 相关分享。

推文内容：
{tweet_text}

作者：@{author}
链接：{urls}

请按以下结构输出研究报告：

## 内容概述
这条推文分享了什么内容？

## 关键信息
有哪些值得关注的要点？

## 背景补充
（如果需要）搜索补充相关背景信息

## 价值评估
这个分享有什么价值？值得关注吗？"""

# Mapping from category to prompt
RESEARCH_PROMPTS = {
    "paper": PAPER_RESEARCH_PROMPT,
    "repo": REPO_RESEARCH_PROMPT,
    "blog": BLOG_RESEARCH_PROMPT,
    "tool": TOOL_RESEARCH_PROMPT,
    "product": TOOL_RESEARCH_PROMPT,  # Use same as tool
    "sharing": SHARING_RESEARCH_PROMPT,
    "other": SHARING_RESEARCH_PROMPT,  # Default to sharing
}


def get_research_prompt(category: str, tweet_text: str, author: str, urls: list[str]) -> str:
    """Get the appropriate research prompt for a category."""
    template = RESEARCH_PROMPTS.get(category.lower(), SHARING_RESEARCH_PROMPT)
    return template.format(
        tweet_text=tweet_text,
        author=author,
        urls=", ".join(urls) if urls else "无链接",
    )
