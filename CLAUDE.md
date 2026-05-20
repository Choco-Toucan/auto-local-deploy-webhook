# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Language

用中文交流，但对于交流中的一些技术关键字，还保持英文。代码使用英文。注释和提交信息使用中文。


## Remote

- GitHub: `git@github.com:Choco-Toucan/auto-local-deploy-webhook.git`
每次提交都要包含变更信息，不要太过冗长，保持简洁，使用中文

每次提交到远端以后，将本次提交的变更内容通过飞书机器人webhook的方式进行发送
webhook的url为：https://open.feishu.cn/open-apis/bot/v2/hook/2a4dabbe-eba5-45f3-92a4-06e695113364

注意，该webhook可能为多个场景使用，所以通知内容要丰富些，使用规范化的消息卡片。


## 概览
这是一个关于实现在ecs机器上通过监听webhook的触发，来从OSS拉取指定的构建产物，并在本地部署/刷新的项目。

本项目通过python3实现，配置从本地配置文件中读取。

webhook的调用方为github actions,传入构建ID和构建产物列表，项目会根据构建ID从OSS拉取对应的构建产物，然后根据构建产物列表，在本地部署/刷新对应的项目。
