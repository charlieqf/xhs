参考 test\comment_bot.py 的做法在小红书回复笔记评论。

有以下新需求：
1 关键词从 keywords.json 中读取，参数从config.json中读取
    A 从special_keywords中读取所有关键词，关键词里面如果包含 {city} 则替换成城市名，城市名从 city.json 中读取，每次随机读取{city_count}个城市名，如果关键词包含 {platform} 则替换成平台名，平台名从 platform.json 中读取，每次随机读取{platform_count}个平台名.
    B 从general_keywords中随机读取{keywords_count}个关键词
    C 将 A 和 B 的关键词组合到一起形成关键词列表

2 基于第一步生成的关键词列表开始遍历
3 基于关键词在小红书上搜索笔记,找到前{post_per_keyword}个笔记，将每个笔记的最前面的{min_comment_count}个笔记评论发给gemini 分析哪个最有获取相亲服务的意愿，如果评论数量少于{min_comment_count}个则不处理。
4 给该评论回复一条邀请对方发私信的消息，简单介绍我们的服务"为单身人群提供真诚靠谱的脱单交友服务，精准匹配同频对象，拓展社交圈，告别无效相亲与低效尬聊。",字数控制在50字以内。

