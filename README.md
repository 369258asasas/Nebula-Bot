星云框架 (Nebula Bot) v8.0.0 开发文档

目录

1. 框架简介
2. 快速开始
3. 配置说明
4. 插件开发指南
5. API 使用方法
6. 共享状态与权柄系统
7. 注意事项
8. 常见问题
9. 进阶功能

框架简介

星云框架是基于 OneBot 协议的 Python 异步机器人框架，专为 Linux 和 Android 系统设计，具有高性能、高安全性和强扩展性。

核心特性

· 🔧 插件化架构 - 支持热加载插件，无需重启机器人
· ⚡ 异步处理 - 基于 asyncio 的高性能异步处理引擎
· 🛡️ 安全机制 - Token 验证、权限控制、安全沙箱
· 📊 状态管理 - 强大的共享状态与权柄系统
· 📝 日志系统 - 分级日志、自动清理、插件独立日志
· 🔄 热重载 - 实时更新插件，开发调试更便捷

技术特性

· 支持 NapCat 后端
· 自动依赖安装
· 请求去重机制
· 事件去重处理
· 启动保护期
· 守护进程模式

快速开始

环境要求

· 系统要求: Linux 或 Android (不支持 Windows/macOS)
· Python 版本: 3.7+
  ```

启动步骤

1. 配置框架
   ```bash
   # 编辑 config.py 文件
   vim config.py
   ```
2. 设置 Token (必须修改)
   ```python
   TOKEN = "your_strong_password_here"  # 长度至少16位，包含大小写字母、数字和特殊字符
   ```
3. 启动框架
   ```bash
   python main.py
   ```

目录结构

```
nebula_framework/
├── main.py              # 主程序入口
├── config.py            # 配置文件
├── api.py               # API 接口封装
├── app.py               # 应用核心
├── server_manager.py    # 服务器管理
├── shared_state.py      # 共享状态管理
├── plugins/             # 插件目录
└── logs/               # 日志目录
```

配置说明

基础配置 (config.py)

```python
# ========== 基础配置 ==========
TOKEN = "your_secret_token_here"  # API认证Token，必须修改！
API_BASE_URL = "http://localhost:3000"  # NapCat服务地址

# 事件服务器配置
EVENT_SERVER_HOST = "127.0.0.1"  # 监听地址
EVENT_SERVER_PORT = 8080         # 监听端口

# ========== 机器人账号配置 ==========
BOT_QQ = 123456789               # 机器人QQ号
ADMIN_QQ = 123456789             # 主人QQ号

# ========== 功能开关 ==========
ENABLE_DEBUG = False             # 调试模式（非开发者勿开）
HOT_RELOAD = True               # 热重载开关
AUTO_INSTALL_MODULES = True     # 自动安装依赖

# ========== 安全配置 ==========
STARTUP_REJECT_EVENTS = True    # 启动期拒绝接收事件
STARTUP_REJECT_DURATION = 20    # 启动保护期时长（秒）

# ========== 性能配置 ==========
ENABLE_REQUEST_DEDUPLICATION = True  # API请求去重
ENABLE_EVENT_DEDUPLICATION = True    # 事件去重
PLUGIN_EVENT_TIMEOUT = 20           # 插件处理超时时间（秒）

# ========== 日志配置 ==========
LOG_LEVEL = "INFO"              # 日志级别
LOG_FILE_MAX_DAYS = 7           # 日志保留天数
```

配置验证

框架启动时会自动验证 Token 强度：

· 长度至少16位
· 包含大小写字母
· 包含数字
· 包含特殊字符

插件开发指南

插件基本结构

每个插件都是一个独立的 Python 文件，放置在 plugins 目录下。

最简单的插件示例

```python
# plugins/hello_plugin.py

class Plugin:
    def __init__(self, context):
        """
        插件初始化
        context: 插件上下文，包含各种工具方法
        """
        self.context = context
        self.logger = context.logger
        self.logger.info("Hello 插件已加载！")
    
    async def handle_event_async(self, event):
        """
        异步处理事件的方法（必须实现）
        event: 收到的事件数据
        """
        if event.get("post_type") == "message":
            self.logger.info(f"收到消息: {event.get('message')}")
```

完整插件模板

```python
# plugins/my_plugin.py
import asyncio
from api import bot_api

class Plugin:
    def __init__(self, context):
        """
        插件初始化
        """
        self.context = context
        self.logger = context.logger
        self.plugin_name = context.plugin_name
        
        # 注册共享变量（权柄）
        self.context.shared.register_var("message_count", 0)
        self.context.shared.register_var("user_data", {})
        
        # 授权其他插件访问本插件的变量（可选）
        # self.context.shared.grant_access_to("other_plugin_name")
        
        self.logger.info(f"{self.plugin_name} 插件初始化完成")
    
    async def handle_event_async(self, event):
        """
        异步处理事件的核心方法
        """
        try:
            post_type = event.get("post_type")
            
            if post_type == "message":
                await self.handle_message(event)
            elif post_type == "notice":
                await self.handle_notice(event)
            elif post_type == "request":
                await self.handle_request(event)
                
        except Exception as e:
            self.logger.error(f"处理事件时出错: {e}")
    
    async def handle_message(self, event):
        """处理消息事件"""
        message_type = event.get("message_type")
        message = event.get("raw_message", "").strip()
        
        # 更新消息计数（使用共享状态）
        count = self.context.shared.get_var("message_count", 0)
        self.context.shared.set_var("message_count", count + 1)
        
        if message_type == "group":
            await self.handle_group_message(event, message)
        elif message_type == "private":
            await self.handle_private_message(event, message)
    
    async def handle_group_message(self, event, message):
        """处理群消息"""
        group_id = event.get("group_id")
        user_id = event.get("user_id")
        
        # 响应命令
        if message == "!hello":
            await bot_api.send_group_msg(group_id, f"你好！用户 {user_id}")
        
        elif message == "!count":
            count = self.context.shared.get_var("message_count", 0)
            await bot_api.send_group_msg(group_id, f"当前消息计数: {count}")
    
    async def handle_private_message(self, event, message):
        """处理私聊消息"""
        user_id = event.get("user_id")
        
        if message == "帮助":
            await bot_api.send_private_msg(user_id, "这是帮助信息...")
    
    async def handle_notice(self, event):
        """处理通知事件"""
        notice_type = event.get("notice_type")
        
        if notice_type == "group_increase":
            # 处理新成员入群
            group_id = event.get("group_id")
            user_id = event.get("user_id")
            self.logger.info(f"新成员 {user_id} 加入群 {group_id}")
    
    async def handle_request(self, event):
        """处理请求事件"""
        request_type = event.get("request_type")
        
        if request_type == "friend":
            # 处理好友请求
            pass
```

插件上下文说明

插件初始化时会收到一个 context 对象，包含以下属性：

· context.plugin_name - 插件名称（自动生成）
· context.logger - 插件专用的日志记录器
· context.global_state - 只读的全局框架状态
· context.shared - 插件的共享状态访问器（权柄）

共享状态（权柄）使用详解

1. 注册共享变量

```python
# 在 __init__ 方法中注册共享变量
self.context.shared.register_var("message_count", 0)
self.context.shared.register_var("user_data", {})
self.context.shared.register_var("last_active", None)
```

2. 基本操作

```python
# 设置变量
self.context.shared.set_var("message_count", 100)

# 获取变量（可设置默认值）
count = self.context.shared.get_var("message_count", 0)

# 获取所有变量
all_vars = self.context.shared.get_all_vars()

# 删除变量
self.context.shared.delete_var("temp_data")

# 清空所有变量
self.context.shared.clear_vars()
```

3. 插件间通信

```python
# 授权其他插件访问本插件的变量
self.context.shared.grant_access_to("weather_plugin")
self.context.shared.grant_access_to("admin_plugin")

# 撤销授权
self.context.shared.revoke_access_from("admin_plugin")

# 获取其他插件的变量（需要授权）
weather_data = self.context.shared.get_other_plugin_var(
    "weather_plugin", 
    "current_weather", 
    "未知"
)
```

4. 权柄安全机制

· 每个插件的变量默认是私有的
· 需要显式授权才能让其他插件访问
· 支持细粒度的权限控制
· 自动进行数据完整性验证

API 使用方法

基础 API 调用

```python
from api import bot_api

# 发送群消息
await bot_api.send_group_msg(group_id, "Hello World!")

# 发送私聊消息
await bot_api.send_private_msg(user_id, "私人消息")

# 发送消息（自动判断类型）
await bot_api.send_msg(message="消息内容", user_id=123, group_id=456)
```

消息相关 API

```python
# 获取消息详情
msg_info = await bot_api.get_msg(message_id)

# 撤回消息
await bot_api.recall_msg(message_id)

# 标记消息已读
await bot_api.mark_msg_as_read(message_id)

# 获取合并转发内容
forward_msg = await bot_api.get_forward_msg(message_id)
```

群管理相关 API

```python
# 获取群列表
group_list = await bot_api.get_group_list()

# 获取群成员列表
members = await bot_api.get_group_member_list(group_id)

# 设置群管理员
await bot_api.set_group_admin(group_id, user_id, enable=True)

# 禁言用户
await bot_api.set_group_ban(group_id, user_id, duration=600)  # 10分钟

# 踢出成员
await bot_api.set_group_kick(group_id, user_id, reject_add_request=False)
```

文件相关 API

```python
# 上传群文件
await bot_api.upload_group_file(group_id, file_path, file_name)

# 获取图片信息
image_info = await bot_api.get_image(file_id)
```

API 响应格式

所有 API 调用返回统一的格式：

```python
{
    "status": "ok",      # "ok" 或 "failed"
    "retcode": 0,        # 返回码，0表示成功
    "data": {...},       # 响应数据
    "msg": "",           # 消息说明
    "wording": ""        # 详细说明
}
```

共享状态与权柄系统

全局状态访问

插件可以读取框架的全局状态信息：

```python
# 获取框架摘要信息
framework_info = self.context.global_state.get_framework_summary()

# 获取特定全局变量
version = self.context.global_state.get_global_var("framework.version")
status = self.context.global_state.get_global_var("framework.status")

# 获取所有全局变量（只读）
all_globals = self.context.global_state.get_all_global_vars()
```

可用的全局状态信息

· 框架信息: 版本、启动时间、运行状态
· 插件统计: 加载数量、拒绝数量、超时次数
· 运行时统计: 运行时长、处理事件数、活跃任务数
· 性能统计: API 请求总数、失败数、插件超时数
· 系统状态: 最后清理时间、最后重载检查、健康状态

注意事项

安全注意事项

1. Token 安全
   · 必须使用强密码作为 Token
   · 长度至少16位，包含大小写字母、数字和特殊字符
   · 定期更换 Token
2. 插件安全
   · 只加载可信来源的插件
   · 定期检查插件代码
   · 使用插件上下文而非直接访问框架内部
3. 系统安全
   · 框架仅支持 Linux 和 Android 系统
   · 不要在 Windows 或 macOS 上运行
   · 使用守护进程模式提高安全性

开发注意事项

1. 异步处理
   ```python
   # ✅ 正确：使用异步函数
   async def handle_event_async(self, event):
       await some_async_operation()
   
   # ❌ 错误：使用阻塞操作
   async def handle_event_async(self, event):
       time.sleep(10)  # 阻塞操作，会冻结整个框架
   ```
2. 错误处理
   ```python
   async def handle_event_async(self, event):
       try:
           # 业务逻辑
           await self.process_message(event)
       except Exception as e:
           self.logger.error(f"处理消息时出错: {e}")
           # 不要抛出异常，避免影响其他插件
   ```
3. 资源管理
   · 及时关闭文件、网络连接等资源
   · 使用共享状态而非全局变量
   · 避免内存泄漏

性能优化

1. 减少 API 调用
   · 使用框架的请求去重机制
   · 合并多个操作
   · 合理使用缓存
2. 优化事件处理
   · 快速处理事件，避免阻塞
   · 使用异步任务处理耗时操作
   · 合理设置超时时间

常见问题

插件加载问题

Q: 插件没有加载怎么办？

A: 检查以下项目：

· 插件文件是否在 plugins 目录下
· 文件名是否以 .py 结尾
· 插件类是否命名为 Plugin
· 是否实现了 handle_event_async 方法
· 查看日志文件中的错误信息

Q: 热重载不生效怎么办？

A: 确保：

· HOT_RELOAD = True
· 插件文件修改后保存
· 等待热重载间隔（默认5秒）
· 检查插件语法是否正确

API 调用问题

Q: API 调用失败怎么办？

A: 排查步骤：

1. 检查 NapCat 服务是否正常运行
2. 验证 Token 配置是否正确
3. 查看 API 日志了解详细错误
4. 检查网络连接

Q: 如何调试插件？

A: 调试方法：

1. 设置 ENABLE_DEBUG = True
2. 查看插件独立日志文件
3. 使用 self.logger.debug() 输出调试信息
4. 检查 logs/ 目录下的日志文件

权柄系统问题

Q: 如何在不同插件间共享数据？

A: 使用共享状态系统：

```python
# 插件A：设置数据并授权
self.context.shared.register_var("shared_data", "hello")
self.context.shared.grant_access_to("plugin_b")

# 插件B：获取数据
data = self.context.shared.get_other_plugin_var("plugin_a", "shared_data")
```

Q: 如何处理插件依赖？

A: 框架支持自动安装依赖：

· 设置 AUTO_INSTALL_MODULES = True
· 在插件中正常 import 所需模块
· 框架会自动检测并安装缺失模块

进阶功能

自定义事件处理

```python
async def handle_event_async(self, event):
    post_type = event.get("post_type")
    
    if post_type == "message":
        await self.handle_message(event)
    elif post_type == "notice":
        await self.handle_notice(event)
    elif post_type == "request":
        await self.handle_request(event)
    elif post_type == "meta_event":
        await self.handle_meta_event(event)

async def handle_notice(self, event):
    """处理通知事件"""
    notice_type = event.get("notice_type")
    
    if notice_type == "group_increase":
        # 处理新成员入群
        group_id = event.get("group_id")
        user_id = event.get("user_id")
        await self.welcome_new_member(group_id, user_id)
    
    elif notice_type == "group_decrease":
        # 处理成员离开
        pass
```

定时任务处理

```python
import asyncio

class Plugin:
    def __init__(self, context):
        self.context = context
        # 启动定时任务
        asyncio.create_task(self.periodic_task())
    
    async def periodic_task(self):
        while True:
            try:
                # 每60秒执行一次的任务
                await self.do_something()
                await asyncio.sleep(60)
            except Exception as e:
                self.context.logger.error(f"定时任务出错: {e}")
                await asyncio.sleep(10)  # 出错后等待10秒重试
    
    async def do_something(self):
        """定时执行的操作"""
        # 例如：清理缓存、发送定时消息等
        pass
```

完整的插件示例：天气查询

```python
# plugins/weather_plugin.py
import aiohttp
import asyncio
from api import bot_api

class Plugin:
    def __init__(self, context):
        self.context = context
        self.logger = context.logger
        
        # 注册共享变量
        self.context.shared.register_var("api_key", "your_weather_api_key")
        self.context.shared.register_var("cache", {})
        
        # 授权其他插件访问天气数据
        self.context.shared.grant_access_to("schedule_plugin")
        
        self.logger.info("天气插件已加载")
    
    async def handle_event_async(self, event):
        if event.get("post_type") == "message":
            message = event.get("raw_message", "").strip()
            
            if message.startswith("!天气"):
                city = message[3:].strip()
                if city:
                    weather_info = await self.get_weather(city)
                    
                    if event.get("message_type") == "group":
                        await bot_api.send_group_msg(
                            event.get("group_id"), 
                            weather_info
                        )
                    else:
                        await bot_api.send_private_msg(
                            event.get("user_id"), 
                            weather_info
                        )
    
    async def get_weather(self, city):
        """获取天气信息"""
        # 检查缓存
        cache = self.context.shared.get_var("cache", {})
        if city in cache:
            return cache[city]
        
        try:
            # 调用天气API
            api_key = self.context.shared.get_var("api_key")
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"https://api.weather.com/{city}?key={api_key}"
                ) as response:
                    data = await response.json()
                    
                    # 解析天气数据
                    weather_text = f"{city}天气: {data['weather']}, 温度: {data['temp']}°C"
                    
                    # 更新缓存
                    cache[city] = weather_text
                    self.context.shared.set_var("cache", cache)
                    
                    return weather_text
                    
        except Exception as e:
            self.logger.error(f"获取天气信息失败: {e}")
            return f"获取 {city} 天气信息失败，请稍后重试"
```

插件生命周期管理

```python
class Plugin:
    def __init__(self, context):
        self.context = context
        self.logger = context.logger
        self._running = True
        
        # 注册清理钩子
        context.register_cleanup(self.cleanup)
        
        self.logger.info("插件已初始化")
    
    async def cleanup(self):
        """插件清理钩子"""
        self._running = False
        self.logger.info("插件正在清理资源...")
        # 执行清理操作，如关闭连接、保存数据等
        await self.save_data()
    
    async def save_data(self):
        """保存插件数据"""
        # 实现数据保存逻辑
        pass
```

---

重要提醒

⚠️ 法律声明与使用条款

1. 禁止事项
   · 严禁使用本框架从事任何非法活动
   · 严禁未经授权转发、分发本框架
   · 严禁擅自修改框架核心代码
2. 责任声明
   · 使用者需自行承担使用本框架带来的所有风险和责任
   · 因违规使用导致的任何损失均由使用者自行承担
3. 系统要求
   · 仅支持 Android (容器) / Linux 系统
   · 不支持 Windows、macOS 等其他系统

请遵守相关法律法规，合理使用机器人框架。不要用于骚扰、spam 或其他不当用途。

如有更多问题，请查看框架日志或联系开发者。

---

版本更新记录

· v8.0.0: 重构共享状态系统，增强安全机制
· v7.0.0: 优化插件生命周期管理
· v6.0.0: 引入权柄系统和插件间通信
· v5.0.0: 增强安全性和稳定性
· v4.0.0: 支持热重载和自动依赖安装
· v3.0.0: 引入异步事件处理
· v2.0.0: 插件化架构重构
· v1.0.0: 初始版本发布

文档最后更新: 2025年
