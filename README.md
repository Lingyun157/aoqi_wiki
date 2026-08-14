# 奥奇传说智能体

一个基于 Vue 3 和 Flask 的奥奇传说游戏数据查询和社区平台。

## 功能模块

- **精灵查询**: 搜索和查看精灵详细信息、技能
- **进化分区**: 查看精灵进化路线和技能数据
- **挑战活动**: 敌方阵容图鉴，支持下载长图
- **攻略专区**: B站视频攻略集成
- **讨论专区**: 论坛系统，支持发帖、评论、点赞
- **用户系统**: 登录、注册、权限管理

## 技术栈

### 前端
- Vue 3 Composition API (CDN)
- 纯 CSS (无框架)
- 响应式设计

### 后端
- Flask (Python)
- MySQL (pymysql)
- HMAC-signed JWT 认证

## 项目结构

```
aoqi/
├── aoqi-agent/           # 主应用
│   ├── frontend/         # 前端代码
│   │   ├── index.html    # 主页面
│   │   ├── js/app.js     # Vue 应用逻辑
│   │   ├── css/style.css # 样式文件
│   │   └── assets/       # 静态资源
│   └── backend/          # 后端代码
│       ├── app.py        # Flask 应用
│       └── database.py   # 数据库配置
├── output/               # 游戏数据输出
│   └── config/pet/       # 精灵配置数据
├── 敌方阵容关卡图鉴/      # 挑战活动数据
├── 灵初精灵知识库/        # 灵初精灵数据
└── 奥奇传说知识库/        # 游戏知识文档
```

## 快速开始

### 环境要求
- Python 3.8+
- MySQL 8.0+
- Node.js (可选，用于开发)

### 安装步骤

1. 克隆仓库
```bash
git clone https://github.com/your-username/aoqi.git
cd aoqi
```

2. 安装 Python 依赖
```bash
pip install flask pymysql requests pillow
```

3. 配置数据库
编辑 `aoqi-agent/backend/database.py`，修改数据库连接参数：
```python
DB_CONFIG = {
    'host': '127.0.0.1',
    'port': 3306,
    'user': 'root',
    'password': 'your_password',
    'database': 'aoqi_forum',
}
```

4. 启动后端服务
```bash
cd aoqi-agent/backend
python app.py
```

5. 访问应用
打开浏览器访问 `http://localhost:5000`

## 默认账户

首次启动会自动创建超级管理员账户：
- 用户名: `admin`
- 密码: `admin123`

## 数据更新

游戏数据通过解包脚本自动更新，存放在 `output/config/pet/` 目录下。后端支持热重载，无需重启服务即可加载最新数据。

## 许可证

MIT License