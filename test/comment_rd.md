test\comment_bot.py 从“相亲平台”关键词查到的10条笔记中，查看前10条评论，如果评论数少于10条，则自动跳过
如果超过10条，则将这10条评论发给大模型，由openrouter 的 google/gemini-3-flash-preview 模型来判断哪个评论最有相亲的意向，同时有针对性的生成一条回复，介绍我们做相亲服务，有需要的话随时联系。
openrouter 的 api-key在 .env 文件中