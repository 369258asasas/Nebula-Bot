import aiohttp
import asyncio
import json
import logging
import os
import time
import hashlib
from config import Config

class BotAPI:
    def __init__(self):
        self.api_base = Config.API_BASE_URL
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {Config.TOKEN}"
        }
        self.session = None
        
        self.logger = logging.getLogger("BotAPI")
        
        if not self.logger.handlers:
            self.logger.setLevel(logging.INFO)
            
            base_dir = os.path.dirname(os.path.abspath(__file__))
            logs_dir = os.path.join(base_dir, 'logs')
            if not os.path.exists(logs_dir):
                os.makedirs(logs_dir)
            
            API_LOG_FILENAME = os.path.join(logs_dir, "bot_api.log")
            
            file_handler = logging.FileHandler(API_LOG_FILENAME, 'a', encoding='utf-8')
            file_handler.setFormatter(logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            ))
            
            console_handler = logging.StreamHandler()
            console_handler.setLevel(logging.INFO)
            console_handler.setFormatter(logging.Formatter(
                '%(asctime)s - %(levelname)s - %(message)s'
            ))
            
            self.logger.addHandler(file_handler)
            self.logger.addHandler(console_handler)
        
        Config.validate_request_deduplication_config()
        
        self.enable_deduplication = Config.ENABLE_REQUEST_DEDUPLICATION
        self.cleanup_interval = Config.REQUEST_CLEANUP_INTERVAL
        self.request_expire_time = Config.REQUEST_EXPIRE_TIME
        self.request_wait_timeout = Config.REQUEST_WAIT_TIMEOUT
        
        self.request_tracker = {}
        self.last_cleanup_time = time.time()
        
        if self.enable_deduplication:
            self.logger.info(f"API请求去重机制: 已启用 (清理间隔: {self.cleanup_interval}秒)")
        else:
            self.logger.info("API请求去重机制: 已禁用")

    async def init_session(self):
        if self.session is None:
            self.session = aiohttp.ClientSession()
    
    async def close_session(self):
        if self.session:
            await self.session.close()
            self.session = None
    
    def _generate_request_id(self, endpoint, params):
        params_str = json.dumps(params, sort_keys=True) if params else "{}"
        request_str = f"{endpoint}_{params_str}"
        return hashlib.md5(request_str.encode('utf-8')).hexdigest()
    
    def _cleanup_old_requests(self):
        if not self.enable_deduplication:
            return
            
        current_time = time.time()
        if current_time - self.last_cleanup_time < self.cleanup_interval:
            return
        
        self.last_cleanup_time = current_time
        expired_requests = []
        
        for request_id, request_data in self.request_tracker.items():
            if current_time - request_data.get("timestamp", 0) > self.request_expire_time:
                expired_requests.append(request_id)
        
        for request_id in expired_requests:
            del self.request_tracker[request_id]
        
        if expired_requests and Config.ENABLE_DEBUG:
            self.logger.debug(f"清理了 {len(expired_requests)} 个过期请求记录")
    
    async def _request_with_retry(self, endpoint, params=None, max_retries=Config.API_REQUEST_MAX_RETRIES):
        await self.init_session()
        url = f"{self.api_base}{endpoint}"
        
        request_id = None
        if self.enable_deduplication:
            request_id = self._generate_request_id(endpoint, params)
            
            self._cleanup_old_requests()
            
            if request_id in self.request_tracker:
                request_data = self.request_tracker[request_id]
                
                if request_data.get("status") == "processing":
                    if Config.ENABLE_DEBUG:
                        self.logger.debug(f"检测到重复的API请求: {endpoint}, 等待结果...")
                    
                    wait_start = time.time()
                    while (time.time() - wait_start < self.request_wait_timeout and 
                           request_id in self.request_tracker and 
                           self.request_tracker[request_id].get("status") == "processing"):
                        await asyncio.sleep(0.1)
                    
                    if (request_id in self.request_tracker and 
                        self.request_tracker[request_id].get("status") != "processing"):
                        if Config.ENABLE_DEBUG:
                            self.logger.debug(f"重复请求已完成，返回缓存结果: {endpoint}")
                        return self.request_tracker[request_id].get("result")
                
                if request_data.get("status") == "completed":
                    if Config.ENABLE_DEBUG:
                        self.logger.debug(f"检测到重复的API请求: {endpoint}, 返回缓存结果")
                    return request_data.get("result")
            
            self.request_tracker[request_id] = {
                "status": "processing",
                "timestamp": time.time(),
                "endpoint": endpoint,
                "params": params
            }
        
        final_result = None
        for attempt in range(max_retries):
            try:
                is_long_operation = endpoint in [
                    "/upload_group_file", 
                    "/upload_private_file",
                    "/download_file",
                    "/get_record",
                    "/get_ai_record"
                ]
                
                timeout_value = Config.API_REQUEST_TIMEOUT_LONG if is_long_operation else Config.API_REQUEST_TIMEOUT_NORMAL
                timeout = aiohttp.ClientTimeout(total=timeout_value)
                
                async with self.session.post(url, headers=self.headers, json=params, timeout=timeout) as response:
                    if response.status in [401, 403]:
                        error_msg = f"API Token验证失败: {url}, 状态码: {response.status}"
                        self.logger.error(error_msg)
                        final_result = {"status": "failed", "retcode": response.status, "error": "Token验证失败"}
                        break
                    
                    result = await response.json()
                
                if result.get("status") == "ok" and result.get("retcode") == 0:
                    final_result = result
                    break
                else:
                    error_msg = f"API调用失败: {result.get('msg')} (返回码: {result.get('retcode')})"
                    
                    if attempt == max_retries - 1:
                        self.logger.error(error_msg)
                        final_result = result
                        break
                    
                    await asyncio.sleep(2 ** attempt)
                    
            except aiohttp.ClientError as e:
                error_msg = f"API请求出错 (尝试 {attempt + 1}/{max_retries}): {str(e)}"
                
                if attempt == max_retries - 1:
                    self.logger.error(error_msg)
                    final_result = {"status": "failed", "retcode": -1, "error": str(e)}
                    break
                
                await asyncio.sleep(2 ** attempt)
                
            except Exception as e:
                error_msg = f"API处理出错 (尝试 {attempt + 1}/{max_retries}): {str(e)}"
                
                if attempt == max_retries - 1:
                    self.logger.error(error_msg)
                    final_result = {"status": "failed", "retcode": -1, "error": str(e)}
                    break
                
                await asyncio.sleep(2 ** attempt)
        
        if final_result is None:
            final_result = {"status": "failed", "retcode": -1, "error": "Max retries exceeded"}
        
        if self.enable_deduplication and request_id:
            if final_result.get("status") == "ok" and final_result.get("retcode") == 0:
                self.request_tracker[request_id] = {
                    "status": "completed",
                    "timestamp": time.time(),
                    "result": final_result
                }
            else:
                del self.request_tracker[request_id]
        
        return final_result
    
    async def _request(self, endpoint, params=None):
        """发送API请求（兼容旧代码）"""
        return await self._request_with_retry(endpoint, params)
    
    # ========== Bot账号相关 ==========
    async def get_login_info(self):
        """获取登录号信息"""
        return await self._request("/get_login_info")
    
    async def set_qq_profile(self, nickname=None, company=None, email=None, college=None, personal_note=None):
        """设置登录号资料"""
        params = {}
        if nickname: params["nickname"] = nickname
        if company: params["company"] = company
        if email: params["email"] = email
        if college: params["college"] = college
        if personal_note: params["personal_note"] = personal_note
        return await self._request("/set_qq_profile", params)
    
    async def qidian_get_account_info(self):
        """获取企点账号信息"""
        return await self._request("/qidian_get_account_info")
    
    async def get_model_show(self, model):
        """获取在线机型"""
        return await self._request("/get_model_show", {"model": model})
    
    async def set_model_show(self, model, model_show):
        """设置在线机型"""
        return await self._request("/set_model_show", {"model": model, "model_show": model_show})
    
    async def get_online_clients(self, no_cache=False):
        """获取当前账号在线客户端列表"""
        return await self._request("/get_online_clients", {"no_cache": no_cache})
    
    # ========== 好友信息 ==========
    async def get_friend_list(self):
        """获取好友列表"""
        return await self._request("/get_friend_list")
    
    async def get_unidirectional_friend_list(self):
        """获取单向好友列表"""
        return await self._request("/get_unidirectional_friend_list")
    
    async def get_stranger_info(self, user_id, no_cache=False):
        """获取陌生人信息"""
        return await self._request("/get_stranger_info", {
            "user_id": user_id,
            "no_cache": no_cache
        })
    
    # ========== 好友操作 ==========
    async def delete_friend(self, user_id):
        """删除好友"""
        return await self._request("/delete_friend", {
            "user_id": user_id
        })
    
    async def delete_unidirectional_friend(self, user_id):
        """删除单向好友"""
        return await self._request("/delete_unidirectional_friend", {
            "user_id": user_id
        })
    
    # ========== 消息相关 ==========
    async def send_private_msg(self, user_id, message, auto_escape=False, group_id=None):
        """发送私聊消息"""
        params = {
            "user_id": user_id,
            "message": message,
            "auto_escape": auto_escape
        }
        if group_id: params["group_id"] = group_id
        return await self._request("/send_private_msg", params)
    
    async def send_group_msg(self, group_id, message, auto_escape=False):
        """发送群消息"""
        return await self._request("/send_group_msg", {
            "group_id": group_id,
            "message": message,
            "auto_escape": auto_escape
        })
    
    async def send_msg(self, message, message_type=None, user_id=None, group_id=None, auto_escape=False):
        """发送消息（自动判断私聊或群聊）"""
        params = {"message": message, "auto_escape": auto_escape}
        if message_type: params["message_type"] = message_type
        if user_id: params["user_id"] = user_id
        if group_id: params["group_id"] = group_id
        return await self._request("/send_msg", params)
    
    async def get_msg(self, message_id):
        """获取消息"""
        return await self._request("/get_msg", {"message_id": message_id})
    
    async def recall_msg(self, message_id):
        """撤回消息"""
        return await self._request("/delete_msg", {
            "message_id": message_id
        })
    
    async def mark_msg_as_read(self, message_id):
        """标记消息已读"""
        return await self._request("/mark_msg_as_read", {"message_id": message_id})
    
    async def get_forward_msg(self, message_id):
        """获取合并转发内容"""
        return await self._request("/get_forward_msg", {"message_id": message_id})
    
    async def send_group_forward_msg(self, group_id, messages):
        """发送合并转发（群聊）"""
        return await self._request("/send_group_forward_msg", {
            "group_id": group_id,
            "messages": messages
        })
    
    async def send_private_forward_msg(self, user_id, messages):
        """发送合并转发（好友）"""
        return await self._request("/send_private_forward_msg", {
            "user_id": user_id,
            "messages": messages
        })
    
    async def get_group_msg_history(self, group_id, message_seq):
        """获取群消息历史记录"""
        return await self._request("/get_group_msg_history", {
            "group_id": group_id,
            "message_seq": message_seq
        })
    
    # ========== 图片相关 ==========
    async def get_image(self, file):
        """获取图片信息"""
        return await self._request("/get_image", {"file": file})
    
    async def can_send_image(self):
        """检查是否可以发送图片"""
        return await self._request("/can_send_image")
    
    async def ocr_image(self, image):
        """图片OCR"""
        return await self._request("/.ocr_image", {"image": image})
    
    # ========== 语音相关 ==========
    async def get_record(self, file, out_format):
        """获取语音"""
        return await self._request("/get_record", {
            "file": file,
            "out_format": out_format
        })
    
    async def can_send_record(self):
        """检查是否可以发送语音"""
        return await self._request("/can_send_record")
    
    # ========== AI语音功能 ==========
    async def get_ai_record(self, character, group_id, text):
        """AI文字转语音"""
        return await self._request("/get_ai_record", {
            "character": character,
            "group_id": group_id,
            "text": text
        })
    
    async def get_ai_characters(self, group_id, chat_type):
        """获取AI语音角色列表"""
        return await self._request("/get_ai_characters", {
            "group_id": group_id,
            "chat_type": chat_type
        })
    
    async def send_group_ai_record(self, character, group_id, text):
        """群聊发送AI语音"""
        return await self._request("/send_group_ai_record", {
            "character": character,
            "group_id": group_id,
            "text": text
        })
    
    # ========== 戳一戳功能 ==========
    async def send_poke(self, user_id, group_id=None):
        """发送戳一戳（群聊/私聊）"""
        params = {"user_id": user_id}
        if group_id:
            params["group_id"] = group_id
        return await self._request("/send_poke", params)
    
    # ========== 处理请求 ==========
    async def set_friend_add_request(self, flag, approve=True, remark=""):
        """处理加好友请求"""
        return await self._request("/set_friend_add_request", {
            "flag": flag,
            "approve": approve,
            "remark": remark
        })
    
    async def set_group_add_request(self, flag, sub_type, approve=True, reason=""):
        """处理加群请求/邀请"""
        return await self._request("/set_group_add_request", {
            "flag": flag,
            "sub_type": sub_type,
            "approve": approve,
            "reason": reason
        })
    
    # ========== 群信息 ==========
    async def get_group_info(self, group_id, no_cache=False):
        """获取群信息"""
        return await self._request("/get_group_info", {
            "group_id": group_id,
            "no_cache": no_cache
        })
    
    async def get_group_list(self, no_cache=False):
        """获取群列表"""
        return await self._request("/get_group_list", {"no_cache": no_cache})
    
    async def get_group_member_info(self, group_id, user_id, no_cache=False):
        """获取群成员信息"""
        return await self._request("/get_group_member_info", {
            "group_id": group_id,
            "user_id": user_id,
            "no_cache": no_cache
        })
    
    async def get_group_member_list(self, group_id, no_cache=False):
        """获取群成员列表"""
        return await self._request("/get_group_member_list", {
            "group_id": group_id,
            "no_cache": no_cache
        })
    
    async def get_group_honor_info(self, group_id, type):
        """获取群荣誉信息"""
        return await self._request("/get_group_honor_info", {
            "group_id": group_id,
            "type": type
        })
    
    async def get_group_system_msg(self):
        """获取群系统消息"""
        return await self._request("/get_group_system_msg")
    
    async def get_essence_msg_list(self, group_id):
        """获取精华消息列表"""
        return await self._request("/get_essence_msg_list", {"group_id": group_id})
    
    async def get_group_at_all_remain(self, group_id):
        """获取群@全体成员剩余次数"""
        return await self._request("/get_group_at_all_remain", {"group_id": group_id})
    
    # ========== 群设置 ==========
    async def set_group_name(self, group_id, group_name):
        """设置群名"""
        return await self._request("/set_group_name", {
            "group_id": group_id,
            "group_name": group_name
        })
    
    async def set_group_portrait(self, group_id, file, cache=1):
        """设置群头像"""
        return await self._request("/set_group_portrait", {
            "group_id": group_id,
            "file": file,
            "cache": cache
        })
    
    async def set_group_admin(self, group_id, user_id, enable=True):
        """设置群管理员"""
        return await self._request("/set_group_admin", {
            "group_id": group_id,
            "user_id": user_id,
            "enable": enable
        })
    
    async def set_group_anonymous(self, group_id, enable=True):
        """设置群匿名聊天"""
        return await self._request("/set_group_anonymous", {
            "group_id": group_id,
            "enable": enable
        })
    
    async def set_group_card(self, group_id, user_id, card=""):
        """设置群名片（群备注）"""
        return await self._request("/set_group_card", {
            "group_id": group_id,
            "user_id": user_id,
            "card": card
        })
    
    async def set_group_leave(self, group_id, is_dismiss=False):
        """退出群组"""
        return await self._request("/set_group_leave", {
            "group_id": group_id,
            "is_dismiss": is_dismiss
        })
    
    async def set_group_special_title(self, group_id, user_id, special_title="", duration=-1):
        """设置群组专属头衔"""
        return await self._request("/set_group_special_title", {
            "group_id": group_id,
            "user_id": user_id,
            "special_title": special_title,
            "duration": duration
        })
    
    async def set_essence_msg(self, message_id):
        """设置精华消息"""
        return await self._request("/set_essence_msg", {"message_id": message_id})
    
    async def delete_essence_msg(self, message_id):
        """移除精华消息"""
        return await self._request("/delete_essence_msg", {"message_id": message_id})
    
    async def send_group_notice(self, group_id, content, image=None):
        """设置群公告"""
        params = {"group_id": group_id, "content": content}
        if image: params["image"] = image
        return await self._request("/_send_group_notice", params)
    
    async def get_group_notice(self, group_id):
        """获取群公告"""
        return await self._request("/_get_group_notice", {"group_id": group_id})
    
    # ========== 群操作 ==========
    async def set_group_kick(self, group_id, user_id, reject_add_request=False):
        """踢出成员"""
        return await self._request("/set_group_kick", {
            "group_id": group_id,
            "user_id": user_id,
            "reject_add_request": reject_add_request
        })
    
    async def set_group_ban(self, group_id, user_id, duration=30 * 60):
        """禁言成员"""
        return await self._request("/set_group_ban", {
            "group_id": group_id,
            "user_id": user_id,
            "duration": duration
        })
    
    async def set_group_whole_ban(self, group_id, enable=True):
        """全体禁言"""
        return await self._request("/set_group_whole_ban", {
            "group_id": group_id,
            "enable": enable
        })
    
    async def set_group_anonymous_ban(self, group_id, anonymous_or_flag, duration=30 * 60):
        """群匿名用户禁言"""
        params = {"group_id": group_id, "duration": duration}
        if isinstance(anonymous_or_flag, dict):
            params["anonymous"] = anonymous_or_flag
        else:
            params["anonymous_flag"] = anonymous_or_flag
        return await self._request("/set_group_anonymous_ban", params)
    
    # ========== 文件相关 ==========
    async def upload_group_file(self, group_id, file, name, folder=None):
        """上传群文件"""
        params = {
            "group_id": group_id,
            "file": file,
            "name": name
        }
        if folder: params["folder"] = folder
        return await self._request("/upload_group_file", params)
    
    async def delete_group_file(self, group_id, file_id, busid):
        """删除群文件"""
        return await self._request("/delete_group_file", {
            "group_id": group_id,
            "file_id": file_id,
            "busid": busid
        })
    
    async def create_group_file_folder(self, group_id, name, parent_id="/"):
        """创建群文件夹"""
        return await self._request("/create_group_file_folder", {
            "group_id": group_id,
            "name": name,
            "parent_id": parent_id
        })
    
    async def delete_group_file_folder(self, group_id, folder_id):
        """删除群文件夹"""
        return await self._request("/delete_group_file_folder", {
            "group_id": group_id,
            "folder_id": folder_id
        })
    
    async def get_group_file_system_info(self, group_id):
        """获取群文件系统信息"""
        return await self._request("/get_group_file_system_info", {"group_id": group_id})
    
    async def get_group_root_files(self, group_id, file_count=50):
        """获取群根目录文件列表"""
        return await self._request("/get_group_root_files", {
            "group_id": group_id,
            "file_count": file_count
        })
    
    async def get_group_files_by_folder(self, group_id, folder_id, file_count=50):
        """获取群子目录文件列表"""
        return await self._request("/get_group_files_by_folder", {
            "group_id": group_id,
            "folder_id": folder_id,
            "file_count": file_count
        })
    
    async def get_group_file_url(self, group_id, file_id, busid):
        """获取群文件资源链接"""
        return await self._request("/get_group_file_url", {
            "group_id": group_id,
            "file_id": file_id,
            "busid": busid
        })
    
    async def upload_private_file(self, user_id, file, name):
        """上传私聊文件"""
        return await self._request("/upload_private_file", {
            "user_id": user_id,
            "file": file,
            "name": name
        })
    
    # ========== Go-CqHttp 相关 ==========
    async def get_version_info(self):
        """获取版本信息"""
        return await self._request("/get_version_info")
    
    async def get_status(self):
        """获取状态"""
        return await self._request("/get_status")
    
    async def reload_event_filter(self, file):
        """重载事件过滤器"""
        return await self._request("/reload_event_filter", {"file": file})
    
    async def download_file(self, url, thread_count=1, headers=None):
        """下载文件到缓存目录"""
        params = {"url": url, "thread_count": thread_count}
        if headers: params["headers"] = headers
        return await self._request("/download_file", params)
    
    async def get_online_clients(self, no_cache=False):
        """获取当前账号在线客户端列表"""
        return await self._request("/get_online_clients", {"no_cache": no_cache})
    
    async def get_word_slices(self, content):
        """获取中文分词"""
        return await self._request("/.get_word_slices", {"content": content})
    
    async def get_group_msg_history(self, group_id, message_seq):
        """获取群消息历史记录"""
        return await self._request("/get_group_msg_history", {
            "group_id": group_id,
            "message_seq": message_seq
        })
    
    async def ocr_image(self, image):
        """图片OCR"""
        return await self._request("/.ocr_image", {"image": image})
    
    async def get_group_system_msg(self):
        """获取群系统消息"""
        return await self._request("/get_group_system_msg")
    
    # ========== 从备份中恢复的接口 ==========
    
    async def get_cookies(self, domain=None):
        """获取Cookies"""
        params = {}
        if domain: params["domain"] = domain
        return await self._request("/get_cookies", params)
    
    async def get_csrf_token(self):
        """获取CSRF Token"""
        return await self._request("/get_csrf_token")
    
    async def get_credentials(self, domain=None):
        """获取QQ相关接口凭证"""
        params = {}
        if domain: params["domain"] = domain
        return await self._request("/get_credentials", params)
    
    async def set_restart(self, delay=2000):
        """重启Go-CqHttp"""
        return await self._request("/set_restart", {"delay": delay})
    
    async def clean_cache(self):
        """清理缓存"""
        return await self._request("/clean_cache")
    
    async def check_url_safely(self, url):
        """检查链接安全性"""
        return await self._request("/check_url_safely", {"url": url})
    
    async def handle_quick_operation(self, context, operation):
        """对事件执行快速操作"""
        return await self._request("/.handle_quick_operation", {
            "context": context,
            "operation": operation
        })
    
    async def like_user(self, user_id, times=1):
        """点赞"""
        return await self._request("/send_like", {
            "user_id": user_id,
            "times": times
        })
    
    async def set_avatar(self, avatar_data):
        """设置头像"""
        return await self._request("/set_qq_avatar", {
            "avatar": avatar_data
        })
    
    async def send_group_sign(self, group_id):
        """群打卡"""
        return await self._request("/send_group_sign", {"group_id": group_id})
    
    async def ban_user(self, group_id, user_id, duration):
        """禁言成员"""
        return await self.set_group_ban(group_id, user_id, duration)
    
    async def unban_user(self, group_id, user_id):
        """解禁成员"""
        return await self.ban_user(group_id, user_id, 0)
    
    async def ban_all(self, group_id):
        """全体禁言"""
        return await self.set_group_whole_ban(group_id, True)
    
    async def unban_all(self, group_id):
        """解除全体禁言"""
        return await self.set_group_whole_ban(group_id, False)
    
    async def kick_user(self, group_id, user_id, block=False):
        """踢出成员"""
        return await self.set_group_kick(group_id, user_id, block)
    
    async def delete_group_folder(self, group_id, folder_id):
        """删除群文件夹"""
        return await self.delete_group_file_folder(group_id, folder_id)
    
    async def __aenter__(self):
        await self.init_session()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close_session()
            
bot_api = BotAPI()