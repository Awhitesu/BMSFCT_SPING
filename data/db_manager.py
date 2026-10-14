import sqlite3
import os
import datetime
import threading
import queue
import sys
from typing import List, Dict, Any, Optional

class DBManager:
    """
    数据存储管理类
    支持 SQLite 数据库持久化存储。
    采用单线程异步写入机制，避免阻塞主线程。
    """
    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            # 不依赖当前工作目录，避免从快捷方式/批处理启动时产生另一份数据库。
            base_dir = (
                os.path.dirname(os.path.abspath(sys.executable))
                if getattr(sys, "frozen", False)
                else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            )
            db_path = os.path.join(base_dir, "bms_test_data.db")
        self.db_path = os.path.abspath(db_path)
        self._init_db()
        self.sequence_start_time = None
        
        # 异步写入队列与后台线程
        self.queue = queue.Queue()
        self.is_running = True
        self._closed = False
        self.worker_thread = threading.Thread(target=self._db_worker, daemon=True)
        self.worker_thread.start()

    def _init_db(self):
        """初始化数据库表结构"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # 测试主表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS test_main (
                test_id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id INTEGER,
                shelf_code TEXT,
                master_code TEXT,
                slave_codes TEXT,
                recipe_name TEXT,
                start_time DATETIME,
                end_time DATETIME,
                result TEXT
            )
        ''')
        
        # 测试数据详细记录表 (采样数据)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS test_details (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                test_id INTEGER,
                step_name TEXT,
                voltage REAL,
                current REAL,
                temp TEXT,
                timestamp DATETIME,
                FOREIGN KEY (test_id) REFERENCES test_main(test_id)
            )
        ''')

        # 测试项判定结果表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS test_items_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                test_id INTEGER,
                item_name TEXT,
                lower_limit REAL,
                upper_limit REAL,
                measured_value REAL,
                result TEXT,
                duration REAL,
                timestamp DATETIME,
                FOREIGN KEY (test_id) REFERENCES test_main(test_id)
            )
        ''')
        
        # 兼容性迁移：检查是否包含 duration 列和 unit 列，若无则 Alter Table 增加
        try:
            cursor.execute("PRAGMA table_info(test_items_results)")
            columns = [col[1] for col in cursor.fetchall()]
            if "duration" not in columns:
                cursor.execute("ALTER TABLE test_items_results ADD COLUMN duration REAL DEFAULT 0.0")
            if "unit" not in columns:
                cursor.execute("ALTER TABLE test_items_results ADD COLUMN unit TEXT DEFAULT 'NULL'")
        except Exception as migration_err:
            print(f"[-] 数据库迁移报错 (兼容列迁移): {migration_err}")
        
        # 系统用户表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS sys_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                role TEXT DEFAULT 'operator',
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        try:
            cursor.execute("PRAGMA table_info(sys_users)")
            cols = [col[1] for col in cursor.fetchall()]
            if "role" not in cols:
                cursor.execute("ALTER TABLE sys_users ADD COLUMN role TEXT DEFAULT 'operator'")
        except Exception as e:
            print(f"[-] sys_users 迁移 role 列报错: {e}")

        cursor.execute("SELECT COUNT(*) FROM sys_users WHERE username = 'ADMIN'")
        if cursor.fetchone()[0] == 0:
            cursor.execute("INSERT OR IGNORE INTO sys_users (username, password, role) VALUES ('ADMIN', 'PSA', 'admin')")
        else:
            cursor.execute("UPDATE sys_users SET role = 'admin' WHERE username = 'ADMIN'")

        # 系统参数持久化表 (保存老化执行次数等)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS sys_params (
                param_key TEXT PRIMARY KEY,
                param_value TEXT,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute("INSERT OR IGNORE INTO sys_params (param_key, param_value) VALUES ('aging_exec_count', '0')")

        conn.commit()
        conn.close()

    def _db_worker(self):
        """后台数据库写入线程"""
        # 在同一个线程内保持一个长连接，提高性能并避免多线程冲突
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        cursor = conn.cursor()
        
        while self.is_running or not self.queue.empty():
            try:
                # 批量处理以提高效率 (最多等待 100ms)
                try:
                    task = self.queue.get(timeout=0.1)
                except queue.Empty:
                    continue

                sql, params, callback_event, result_container = task
                
                if sql is None:
                    # BUG-09修复: 收到None哨兵信号，立即退出
                    break
                try:
                    cursor.execute(sql, params)
                    if "INSERT" in sql.upper():
                        res = cursor.lastrowid
                    else:
                        res = True
                    
                    if result_container is not None:
                        result_container['value'] = res
                    
                    conn.commit()
                except Exception as e:
                    print(f"[-] 数据库执行异常: {e}, SQL: {sql}")
                    if result_container is not None:
                        result_container['error'] = e
                finally:
                    if callback_event:
                        callback_event.set()
                    self.queue.task_done()
                    
            except Exception as e:
                print(f"[-] 数据库后台线程严重错误: {e}")
        
        conn.close()

    def close(self):
        """BUG-09修复: 先投入哨兵信号唤醒工作线程，再 join，防止大量队列时卡住"""
        if self._closed:
            return
        self._closed = True
        self.is_running = False
        self.queue.put((None, None, None, None))  # 哨兵信号
        if self.worker_thread.is_alive():
            self.worker_thread.join(timeout=5.0)

    def _execute_async(self, sql: str, params: tuple = (), wait: bool = False) -> Any:
        """内部通用异步执行方法"""
        if self._closed:
            raise RuntimeError("数据库管理器已关闭")
        event = threading.Event() if wait else None
        container = {'value': None, 'error': None} if wait else None
        
        self.queue.put((sql, params, event, container))
        
        if wait:
            if not event.wait(timeout=5.0):
                raise TimeoutError("数据库操作超时")
            if container['error']:
                raise container['error']
            return container['value']
        return None

    def start_new_test(self, channel_id: int, shelf: str, sn: str, recipe: str) -> int:
        """记录测试开始并返回测试 ID (同步等待结果)"""
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sql = '''
            INSERT INTO test_main (channel_id, shelf_code, master_code, slave_codes, recipe_name, start_time, result)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        '''
        params = (channel_id, shelf, sn, "", recipe, now, "RUNNING")
        return self._execute_async(sql, params, wait=True)

    def log_detail(self, test_id: int, step_name: str, voltage: float, current: float, temp: str):
        """记录实时采样数据 (异步不等待)"""
        now = datetime.datetime.now().strftime("%H:%M:%S")
        sql = '''
            INSERT INTO test_details (test_id, step_name, voltage, current, temp, timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
        '''
        params = (test_id, step_name, voltage, current, temp, now)
        self._execute_async(sql, params, wait=False)

    def log_item_result(self, test_id: int, name: str, low: float, high: float, val: float, res: str, duration: float = 0.0, unit: str = "NULL"):
        """记录某个测试项目的最终判定结果 (异步不等待)"""
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sql = '''
            INSERT INTO test_items_results (test_id, item_name, lower_limit, upper_limit, measured_value, result, duration, unit, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        '''
        params = (test_id, name, low, high, val, res, duration, unit, now)
        self._execute_async(sql, params, wait=False)

    def finish_test(self, test_id: int, result: str):
        """记录测试结束 (同步等待以确保后续文件导出正确)"""
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sql = 'UPDATE test_main SET end_time = ?, result = ? WHERE test_id = ?'
        params = (now, result, test_id)
        return self._execute_async(sql, params, wait=True)
        

    # --- 配方 (Recipe) JSON 存储逻辑 ---
    
    def get_recipe_dir(self):
        recipe_dir = os.path.join(os.path.dirname(self.db_path), "recipes")
        os.makedirs(recipe_dir, exist_ok=True)
        return recipe_dir

    def save_recipe_json(self, name: str, data: Dict[str, Any]) -> bool:
        try:
            import json
            file_path = os.path.join(self.get_recipe_dir(), f"{name}.json")
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
            return True
        except: return False

    def load_recipe_json(self, name: str) -> Optional[Dict[str, Any]]:
        try:
            import json
            file_path = os.path.join(self.get_recipe_dir(), f"{name}.json")
            if not os.path.exists(file_path): return None
            with open(file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except: return None

    def list_recipes(self) -> List[str]:
        try:
            recipe_dir = self.get_recipe_dir()
            files = [f for f in os.listdir(recipe_dir) if f.endswith(".json")]
            return [os.path.splitext(f)[0] for f in files]
        except: return []

    def rename_recipe(self, old_name: str, new_name: str) -> bool:
        """重命名文件及内部名称，拒绝覆盖其他配方。"""
        import json
        import re
        new_name = new_name.strip()
        for name in (old_name, new_name):
            if (not name or name in ('.', '..') or name.endswith(('.', ' '))
                    or re.search(r'[<>:"/\\|?*\x00-\x1f]', name)
                    or name.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL',
                        *[f'COM{i}' for i in range(1, 10)], *[f'LPT{i}' for i in range(1, 10)]}):
                return False
        if old_name == new_name:
            return True
        directory = self.get_recipe_dir()
        source = os.path.join(directory, old_name + '.json')
        target = os.path.join(directory, new_name + '.json')
        if os.path.exists(target):
            return False
        created = False
        try:
            with open(source, encoding='utf-8') as stream:
                data = json.load(stream)
            data['name'] = new_name
            with open(target, 'x', encoding='utf-8') as stream:
                created = True
                json.dump(data, stream, ensure_ascii=False, indent=4)
            os.remove(source)
            return True
        except Exception:
            if created:
                os.remove(target)
            return False

    def delete_recipe(self, name: str) -> bool:
        try:
            file_path = os.path.join(self.get_recipe_dir(), f"{name}.json")
            if os.path.exists(file_path):
                os.remove(file_path)
                return True
            return False
        except: return False

    # --- 系统与硬件配置存储逻辑 ---
    
    def get_config_dir(self):
        config_dir = os.path.join(os.path.dirname(self.db_path), "config")
        os.makedirs(config_dir, exist_ok=True)
        return config_dir

    def save_sys_config(self, data: Dict[str, Any]) -> bool:
        try:
            import json
            file_path = os.path.join(self.get_config_dir(), "sys_config.json")
            existing = {}
            if os.path.exists(file_path):
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        existing = json.load(f)
                except:
                    pass
            existing.update(data)
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(existing, f, indent=4, ensure_ascii=False)
            return True
        except: return False

    def load_sys_config(self) -> Dict[str, Any]:
        try:
            import json
            file_path = os.path.join(self.get_config_dir(), "sys_config.json")
            if not os.path.exists(file_path): return {}
            with open(file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except: return {}

    def save_channel_config(self, data: List[Dict[str, Any]]) -> bool:
        """保存通道与货架映射、通道板 IP 等配置"""
        try:
            import json
            file_path = os.path.join(self.get_config_dir(), "channel_config.json")
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
            return True
        except: return False

    def load_channel_config(self) -> List[Dict[str, Any]]:
        try:
            import json
            file_path = os.path.join(self.get_config_dir(), "channel_config.json")
            if not os.path.exists(file_path): return []
            with open(file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except: return []

    # ================= 用户权限管理与老化计数 =================
    def get_aging_exec_count(self) -> int:
        """读取历史老化执行累计次数"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT param_value FROM sys_params WHERE param_key = 'aging_exec_count'")
                row = cursor.fetchone()
                return int(row[0]) if row and row[0] else 0
        except Exception as e:
            print(f"[-] 读取老化执行次数失败: {e}")
            return 0

    def increment_aging_exec_count(self) -> int:
        """老化工步执行开始时自增 1，持久化并返回最新次数"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO sys_params (param_key, param_value, updated_at) 
                    VALUES ('aging_exec_count', '1', datetime('now', 'localtime'))
                    ON CONFLICT(param_key) DO UPDATE SET 
                        param_value = CAST(CAST(param_value AS INTEGER) + 1 AS TEXT),
                        updated_at = datetime('now', 'localtime')
                """)
                conn.commit()
                cursor.execute("SELECT param_value FROM sys_params WHERE param_key = 'aging_exec_count'")
                row = cursor.fetchone()
                new_val = int(row[0]) if row and row[0] else 1
                print(f"[DB] 老化执行计数原子自增成功，当前最新累计次数: {new_val}")
                return new_val
        except Exception as e:
            print(f"[-] 自增老化执行次数失败: {e}")
            return self.get_aging_exec_count()

    def get_all_users(self) -> List[str]:
        """获取所有可用用户名列表"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT username FROM sys_users ORDER BY id ASC")
                return [row[0] for row in cursor.fetchall()]
        except Exception as e:
            print(f"[-] 查询用户列表失败: {e}")
            return ["ADMIN"]

    def get_all_users_with_role(self) -> List[Dict[str, str]]:
        """获取所有用户及其权限身份列表"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT username, role FROM sys_users ORDER BY id ASC")
                return [{"username": row[0], "role": (row[1] or "operator").lower()} for row in cursor.fetchall()]
        except Exception as e:
            print(f"[-] 查询用户及角色失败: {e}")
            return [{"username": "ADMIN", "role": "admin"}]

    def get_user_role(self, username: str) -> str:
        """获取指定用户的角色权限 ('admin' / 'operator')"""
        u = (username or "").strip()
        if u.upper() == "ADMIN":
            return "admin"
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT role FROM sys_users WHERE username = ?", (u,))
                row = cursor.fetchone()
                if row and row[0]:
                    return row[0].lower()
        except Exception as e:
            print(f"[-] 查询用户角色失败: {e}")
        return "operator"

    def verify_user(self, username: str, password: str) -> bool:
        """验证用户名和密码"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT password FROM sys_users WHERE username = ?", (username.strip(),))
                row = cursor.fetchone()
                if row and row[0] == password:
                    return True
                return False
        except Exception as e:
            print(f"[-] 验证用户密码失败: {e}")
            return False

    def update_user(self, old_username: str, new_username: str, new_password: str) -> tuple[bool, str]:
        """修改账户的用户名和密码"""
        old_u = old_username.strip()
        new_u = new_username.strip()
        if not new_u:
            return False, "新用户名不能为空"
        if not new_password:
            return False, "新密码不能为空"
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                if old_u != new_u:
                    cursor.execute("SELECT COUNT(*) FROM sys_users WHERE username = ?", (new_u,))
                    if cursor.fetchone()[0] > 0:
                        return False, f"用户名 '{new_u}' 已存在"
                cursor.execute("""
                    UPDATE sys_users 
                    SET username = ?, password = ?, updated_at = datetime('now', 'localtime')
                    WHERE username = ?
                """, (new_u, new_password, old_u))
                conn.commit()
                return True, "修改成功"
        except Exception as e:
            return False, f"数据库更新失败: {e}"

    def delete_user(self, username: str) -> tuple[bool, str]:
        """删除账户 (超级管理员 ADMIN 严禁删除)"""
        u = (username or "").strip()
        if not u:
            return False, "请选择要删除的账户"
        if u.upper() == "ADMIN":
            return False, "超级管理员 ADMIN 为系统内置核心账户，不可删除！"
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM sys_users WHERE username = ?", (u,))
                conn.commit()
                return True, f"账户 '{u}' 已成功删除！"
        except Exception as e:
            return False, f"删除账户失败: {e}"

    def add_user(self, username: str, password: str, role: str = "operator") -> tuple[bool, str]:
        """新增账户并指定权限 (admin=管理员, operator=操作员)"""
        u = username.strip()
        if not u:
            return False, "用户名不能为空"
        if not password:
            return False, "密码不能为空"
        role_val = "admin" if role in ["admin", "管理员"] else "operator"
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM sys_users WHERE username = ?", (u,))
                if cursor.fetchone()[0] > 0:
                    return False, f"用户名 '{u}' 已存在"
                cursor.execute("""
                    INSERT INTO sys_users (username, password, role, updated_at)
                    VALUES (?, ?, ?, datetime('now', 'localtime'))
                """, (u, password, role_val))
                conn.commit()
                return True, "创建成功"
        except Exception as e:
            return False, f"添加用户失败: {e}"


