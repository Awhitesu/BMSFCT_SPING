import sqlite3
import os
import datetime
import threading
import queue
import sys
import re
import math
import json
from contextlib import closing
from pathlib import Path
from core.passwords import hash_password, verify_password, PREFIX
from typing import List, Dict, Any, Optional


def _json_record_value(value):
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_record_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_record_value(item) for item in value]
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex(' ').upper()
    return value

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
        self._config_lock = threading.RLock()
        self._write_errors = []
        self._write_error_lock = threading.Lock()
        self._init_db()
        self.sequence_start_time = None
        
        # 异步写入队列与后台线程
        self.queue = queue.Queue()
        self.is_running = True
        self._closed = False
        self._queue_lock = threading.Lock()
        self.worker_thread = threading.Thread(target=self._db_worker, daemon=True)
        self.worker_thread.start()

    def _init_db(self):
        """初始化数据库表结构"""
        conn = sqlite3.connect(self.db_path)
        conn.execute('''CREATE TABLE IF NOT EXISTS statistics_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL, operator TEXT NOT NULL,
            action TEXT NOT NULL, details_json TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS project_statistics_state (
            project_name TEXT PRIMARY KEY, cutoff_test_id INTEGER NOT NULL DEFAULT 0)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS project_identity (
            project_name TEXT PRIMARY KEY, project_id TEXT NOT NULL UNIQUE)''')
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
        
        cursor.execute('PRAGMA table_info(test_main)')
        if 'operator' not in {row[1] for row in cursor.fetchall()}:
            cursor.execute('ALTER TABLE test_main ADD COLUMN operator TEXT')
        cursor.execute('PRAGMA table_info(test_main)')
        if 'context_json' not in {row[1] for row in cursor.fetchall()}:
            cursor.execute('ALTER TABLE test_main ADD COLUMN context_json TEXT')
        if 'project_id' not in {row[1] for row in cursor.execute('PRAGMA table_info(test_main)').fetchall()}:
            cursor.execute('ALTER TABLE test_main ADD COLUMN project_id TEXT')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_test_main_project_id ON test_main(project_id,test_id)')
        cursor.execute("""CREATE INDEX IF NOT EXISTS idx_test_main_statistics
            ON test_main(start_time,test_id) WHERE end_time IS NOT NULL
            AND result IN ('PASS','NG','FAIL')""")
        cursor.execute("""CREATE INDEX IF NOT EXISTS idx_test_main_project_statistics
            ON test_main(recipe_name,test_id) WHERE end_time IS NOT NULL
            AND result IN ('PASS','NG','FAIL')""")

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
        
        cursor.execute("PRAGMA table_info(test_items_results)")
        result_columns = {row[1] for row in cursor.fetchall()}
        for column in ('execution_id', 'step_id'):
            if column not in result_columns:
                cursor.execute(f"ALTER TABLE test_items_results ADD COLUMN {column} TEXT")
        if 'attempt' not in result_columns:
            cursor.execute("ALTER TABLE test_items_results ADD COLUMN attempt INTEGER DEFAULT 1")
        for column in ('lower_limit_raw','upper_limit_raw','measured_value_raw'):
            if column not in result_columns:cursor.execute(f'ALTER TABLE test_items_results ADD COLUMN {column} TEXT')

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
            if "password_reset_required" not in cols:
                cursor.execute("ALTER TABLE sys_users ADD COLUMN password_reset_required INTEGER DEFAULT 0")
        except Exception as e:
            print(f"[-] sys_users 迁移 role 列报错: {e}")

        cursor.execute("SELECT COUNT(*) FROM sys_users WHERE username = 'ADMIN'")
        if cursor.fetchone()[0] == 0:
            cursor.execute("INSERT OR IGNORE INTO sys_users (username, password, role, password_reset_required) VALUES (?, ?, ?, 1)", ("ADMIN", hash_password("PSA"), "admin"))
        else:
            cursor.execute("UPDATE sys_users SET role = 'admin' WHERE username = 'ADMIN'")
            cursor.execute("UPDATE sys_users SET password_reset_required=1 WHERE username='ADMIN' AND password='PSA'")

        # 系统参数持久化表 (保存系统参数)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS sys_params (
                param_key TEXT PRIMARY KEY,
                param_value TEXT,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        cursor.execute("""CREATE TABLE IF NOT EXISTS test_step_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, test_id INTEGER,
            execution_id TEXT, step_id TEXT, parent_step_id TEXT,
            attempt INTEGER, status TEXT, value_json TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)""")

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
                    self.queue.task_done()
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
                    conn.rollback()
                    with self._write_error_lock:
                        self._write_errors.append((params[0] if params else None, str(e)))
                    print(f"[-] Database write failed: {e}")
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
        with self._queue_lock:
            if not self._closed:
                self._closed = True
                self.is_running = False
                self.queue.put((None, None, None, None))
        if self.worker_thread.is_alive():
            self.worker_thread.join(timeout=5.0)
        if self.worker_thread.is_alive():
            raise TimeoutError('Database shutdown is still draining pending records')
        if self.queue.unfinished_tasks:
            raise RuntimeError('Database worker exited with uncommitted queued records')

    def _execute_async(self, sql: str, params: tuple = (), wait: bool = False) -> Any:
        """内部通用异步执行方法"""
        event = threading.Event() if wait else None
        container = {'value': None, 'error': None} if wait else None
        with self._queue_lock:
            if self._closed:
                raise RuntimeError('Database manager has closed')
            self.queue.put((sql, params, event, container))

        if wait:
            if not event.wait(timeout=5.0):
                raise TimeoutError("数据库操作超时")
            if container['error']:
                raise container['error']
            return container['value']
        return None

    def start_new_test(self, channel_id: int, shelf: str, sn: str, recipe: str, operator=None, context=None) -> int:
        """记录测试开始并返回测试 ID (同步等待结果)"""
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sql = '''
            INSERT INTO test_main (channel_id, shelf_code, master_code, slave_codes, recipe_name, start_time, result, operator, context_json,project_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        '''
        from core.user_session import get_current_user
        evidence = json.dumps(context,ensure_ascii=False,allow_nan=False) if context is not None else None
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute('BEGIN IMMEDIATE')
            project_id=self._ensure_project_identity(conn,recipe)
        params = (channel_id, shelf, sn, "", recipe, now, "RUNNING", operator if operator is not None else get_current_user(), evidence,project_id)
        return self._execute_async(sql, params, wait=True)

    def load_production_statistics(self, start, end, include_cleared=False):
        """Canonical completed records; reset excludes earlier database identities."""
        from contextlib import closing
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute('BEGIN')
            row=conn.execute("SELECT param_value FROM sys_params WHERE param_key='stats_reset_test_id'").fetchone()
            cutoff=int(row[0]) if row and not include_cleared else 0
            return conn.execute("""SELECT master_code, result, test_id FROM test_main
                WHERE test_id>? AND start_time>=? AND start_time<? AND end_time IS NOT NULL
                AND result IN ('PASS','NG','FAIL') ORDER BY test_id""",(cutoff,start,end)).fetchall()

    def start_traced_test(self, channel_id, shelf, sn, recipe, operator=None):
        from core.run_context import build_run_context
        context=build_run_context(recipe,self.load_sys_config())
        return self.start_new_test(channel_id,shelf,sn,recipe.get('name','ManualTest'),
                                   operator=operator,context=context)

    def record_statistics_audit(self, action, details, operator=None):
        from core.user_session import get_current_user
        self._execute_async('INSERT INTO statistics_audit(created_at,operator,action,details_json) VALUES(?,?,?,?)',
            (datetime.datetime.now().astimezone().isoformat(),operator or get_current_user() or '未登录',action,
             json.dumps(dict(details,db_path=self.db_path),ensure_ascii=False,allow_nan=False)),wait=False)

    @staticmethod
    def _ensure_project_identity(conn,project):
        import uuid
        row=conn.execute('SELECT project_id FROM project_identity WHERE project_name=?',(project,)).fetchone()
        if row:identity=row[0]
        else:
            identity=uuid.uuid4().hex
            conn.execute('INSERT INTO project_identity(project_name,project_id) VALUES(?,?)',(project,identity))
            conn.execute('''INSERT OR IGNORE INTO project_statistics_state(project_name,cutoff_test_id)
                SELECT ?,cutoff_test_id FROM project_statistics_state WHERE project_name=?''',(identity,project))
            conn.execute('DELETE FROM project_statistics_state WHERE project_name=?',(project,))
        conn.execute('UPDATE test_main SET project_id=? WHERE project_id IS NULL AND recipe_name=?',(identity,project))
        return identity

    @staticmethod
    def _project_statistics_cutoff(conn,project):
        row=conn.execute('SELECT cutoff_test_id FROM project_statistics_state WHERE project_name=?',(project,)).fetchone()
        if row:return int(row[0])
        # Respect a clear performed before project statistics were introduced.
        row=conn.execute("SELECT param_value FROM sys_params WHERE param_key='stats_reset_test_id'").fetchone()
        return int(row[0]) if row else 0

    def load_project_statistics(self,project):
        """Lifetime completed results for exactly one recipe, after its clear."""
        from contextlib import closing
        if not isinstance(project,str) or not project.strip():return []
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute('BEGIN IMMEDIATE')
            identity=self._ensure_project_identity(conn,project)
            cutoff=self._project_statistics_cutoff(conn,identity)
            rows=conn.execute("""SELECT master_code,result,test_id FROM test_main
                WHERE project_id=? AND test_id>? AND end_time IS NOT NULL
                AND result IN ('PASS','NG','FAIL') ORDER BY test_id""",(identity,cutoff)).fetchall()
            conn.commit()
            return rows

    def reset_project_statistics(self,project):
        if not isinstance(project,str) or not project.strip():raise ValueError('请先选择需要清零的项目')
        from core.user_session import get_current_user
        from core.production_statistics import ProductionStatistics
        self._execute_async('SELECT 1',wait=True)
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute('BEGIN IMMEDIATE')
            identity=self._ensure_project_identity(conn,project)
            previous=self._project_statistics_cutoff(conn,identity)
            cutoff=conn.execute('SELECT COALESCE(MAX(test_id),0) FROM test_main WHERE project_id=?',(identity,)).fetchone()[0]
            cutoff=max(previous,cutoff)
            rows=conn.execute("""SELECT master_code,result,test_id FROM test_main
                WHERE project_id=? AND test_id>? AND end_time IS NOT NULL
                AND result IN ('PASS','NG','FAIL') ORDER BY test_id""",(identity,previous)).fetchall()
            stats=ProductionStatistics();stats.restore(rows)
            evidence=dict(project=project,project_id=identity,previous_cutoff=previous,new_cutoff=cutoff,
                before_deduplicated=stats.counts(True),before_attempts=stats.counts(False),db_path=self.db_path)
            conn.execute('INSERT INTO statistics_audit(created_at,operator,action,details_json) VALUES(?,?,?,?)',
                (datetime.datetime.now().astimezone().isoformat(),get_current_user() or '未登录','project_reset_committed',json.dumps(evidence,ensure_ascii=False)))
            conn.execute('''INSERT INTO project_statistics_state(project_name,cutoff_test_id) VALUES(?,?)
                ON CONFLICT(project_name) DO UPDATE SET cutoff_test_id=excluded.cutoff_test_id''',(identity,cutoff))
        return True

    def reset_production_statistics(self):
        """Commit the reset boundary and its evidence in one transaction."""
        from core.user_session import get_current_user
        from core.production_statistics import ProductionStatistics
        self._execute_async('SELECT 1',wait=True)  # Drain earlier queued writes.
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute('BEGIN IMMEDIATE')
            row=conn.execute("SELECT param_value FROM sys_params WHERE param_key='stats_reset_test_id'").fetchone()
            previous=int(row[0]) if row else 0
            cutoff=conn.execute('SELECT COALESCE(MAX(test_id),0) FROM test_main').fetchone()[0]
            rows=conn.execute("SELECT master_code,result,test_id FROM test_main WHERE test_id>? AND end_time IS NOT NULL AND result IN ('PASS','NG','FAIL') ORDER BY test_id",(previous,)).fetchall()
            stats=ProductionStatistics();stats.restore(rows)
            evidence=dict(previous_cutoff=previous,new_cutoff=cutoff,
                before_deduplicated=stats.counts(True),before_attempts=stats.counts(False),db_path=self.db_path)
            conn.execute('INSERT INTO statistics_audit(created_at,operator,action,details_json) VALUES(?,?,?,?)',
                (datetime.datetime.now().astimezone().isoformat(),get_current_user() or '未登录','reset_committed',json.dumps(evidence,ensure_ascii=False)))
            conn.execute("""INSERT INTO sys_params(param_key,param_value) VALUES('stats_reset_test_id',?)
                ON CONFLICT(param_key) DO UPDATE SET param_value=excluded.param_value""",(str(cutoff),))
        return True

    def log_detail(self, test_id: int, step_name: str, voltage: float, current: float, temp: str):
        """记录实时采样数据 (异步不等待)"""
        now = datetime.datetime.now().strftime("%H:%M:%S")
        sql = '''
            INSERT INTO test_details (test_id, step_name, voltage, current, temp, timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
        '''
        params = (test_id, step_name, voltage, current, temp, now)
        self._execute_async(sql, params, wait=False)

    def log_item_result(self, test_id: int, name: str, low: float, high: float, val: float, res: str, duration: float = 0.0, unit: str = "NULL", execution_id=None, step_id=None, attempt=1):
        """记录某个测试项目的最终判定结果 (异步不等待)"""
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sql = '''
            INSERT INTO test_items_results (test_id, item_name, lower_limit, upper_limit, measured_value, result, duration, unit, timestamp, execution_id, step_id, attempt, lower_limit_raw, upper_limit_raw, measured_value_raw)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        '''
        def stored(value):
            # SQLite silently converts floating NaN to NULL; retain invalid raw
            # measurements as evidence instead of losing the original value.
            if isinstance(value, bool) or isinstance(value, float) and not math.isfinite(value):
                return str(value)
            if isinstance(value, (dict, list, tuple)):
                return json.dumps(_json_record_value(value), ensure_ascii=False, default=str, allow_nan=False)
            if isinstance(value, (bytes, bytearray, memoryview)):
                return _json_record_value(value)
            return value
        low,high,val=stored(low),stored(high),stored(val)
        params = (test_id, name, low, high, val, res, duration, unit, now, execution_id, step_id, attempt,
                  None if low is None else str(low),None if high is None else str(high),None if val is None else str(val))
        self._execute_async(sql, params, wait=False)

    def log_step_event(self, test_id, event):
        self._execute_async(
            "INSERT INTO test_step_events (test_id,execution_id,step_id,parent_step_id,attempt,status,value_json) VALUES (?,?,?,?,?,?,?)",
            (test_id, event['execution_id'], event['step_id'], event['parent_step_id'],
             event['attempt'], event['status'], json.dumps(_json_record_value(event['value']), ensure_ascii=False, default=str, allow_nan=False)),
            wait=False)

    def abort_prepared_test(self, test_id):
        self._execute_async("UPDATE test_main SET result='STOPPED',end_time=datetime('now','localtime') WHERE test_id=?", (test_id,),wait=False)

    def finish_test(self, test_id: int, result: str):
        """记录测试结束 (同步等待以确保后续文件导出正确)"""
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sql = 'UPDATE test_main SET end_time = ?, result = ? WHERE test_id = ?'
        params = (now, result, test_id)
        try:
            result = self._execute_async(sql, params, wait=True)
        except Exception:
            # A timed-out UPDATE may still be queued. Put the failure marker
            # behind it so a late commit cannot turn RECORD ERROR into PASS.
            try:
                self._execute_async('UPDATE test_main SET result=? WHERE test_id=?', ('RECORD_ERROR',test_id), wait=False)
            except Exception:
                pass
            raise
        with self._write_error_lock:
            errors = [error for identity, error in self._write_errors if identity == test_id]
        if errors:
            self._execute_async('UPDATE test_main SET result=? WHERE test_id=?', ('RECORD_ERROR',test_id), wait=True)
            raise RuntimeError('Test record persistence failed: ' + '; '.join(errors))
        return result


    # --- 配方 (Recipe) JSON 存储逻辑 ---
    
    def get_recipe_dir(self):
        recipe_dir = os.path.join(os.path.dirname(self.db_path), "recipes")
        os.makedirs(recipe_dir, exist_ok=True)
        return recipe_dir

    def _recipe_path(self, name):
        if (not isinstance(name, str) or not name or name in ('.', '..')
                or name.endswith(('.', ' ')) or len(name) > 128
                or re.search(r'[<>:"/\\|?*\x00-\x1f]', name)
                or name.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL',
                    *[f'COM{i}' for i in range(1, 10)], *[f'LPT{i}' for i in range(1, 10)]}):
            raise ValueError('Invalid recipe filename')
        root = Path(self.get_recipe_dir()).resolve()
        path = root / (name + '.json')
        if not path.resolve().is_relative_to(root):
            raise ValueError('Recipe must stay inside its directory')
        return str(path)

    def save_recipe_json(self, name: str, data: Dict[str, Any]) -> bool:
        try:
            file_path = self._recipe_path(name)
            import json
            from core.recipe_identity import ensure_step_ids
            ensure_step_ids(data)
            import tempfile
            fd, temporary = tempfile.mkstemp(dir=self.get_recipe_dir(), suffix='.tmp')
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=4, ensure_ascii=False)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(temporary, file_path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            return True
        except: return False

    def load_recipe_json(self, name: str) -> Optional[Dict[str, Any]]:
        try:
            import json
            file_path = self._recipe_path(name)
            if not os.path.exists(file_path): return None
            with open(file_path, "r", encoding="utf-8") as f:
                from core.recipe_identity import ensure_step_ids
                data = json.load(f)
            before = json.dumps(data, sort_keys=True)
            ensure_step_ids(data)
            if json.dumps(data, sort_keys=True) != before:
                if not self.save_recipe_json(name, data):
                    raise OSError("Cannot persist recipe identity migration")
            return data
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
        if not isinstance(new_name, str):
            return False
        new_name = new_name.strip()
        try:
            source, target = self._recipe_path(old_name), self._recipe_path(new_name)
        except (ValueError, OSError):
            return False
        if old_name == new_name:
            return os.path.isfile(source)
        if os.path.exists(target):
            return False
        created = False
        original=None
        try:
            with open(source,'rb') as stream:original=stream.read()
            data=json.loads(original.decode('utf-8'))
            data['name'] = new_name
            with open(target, 'x', encoding='utf-8') as stream:
                created = True
                json.dump(data, stream, ensure_ascii=False, indent=4)
            self._execute_async('SELECT 1',wait=True)
            with closing(sqlite3.connect(self.db_path)) as conn, conn:
                conn.execute('BEGIN IMMEDIATE')
                identity=self._ensure_project_identity(conn,old_name)
                existing=conn.execute('SELECT project_id FROM project_identity WHERE project_name=?',(new_name,)).fetchone()
                if existing and existing[0]!=identity:raise ValueError('目标名称已有其他项目的统计身份')
                conn.execute('UPDATE project_identity SET project_name=? WHERE project_id=?',(new_name,identity))
                os.remove(source)
            return True
        except Exception:
            if original is not None and not os.path.exists(source):
                with open(source,'xb') as stream:stream.write(original)
            if created:
                os.remove(target)
            return False

    def delete_recipe(self, name: str) -> bool:
        try:
            file_path = self._recipe_path(name)
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
        from core import system_config
        try:
            system_config.update(data, os.path.join(self.get_config_dir(), "sys_config.json"))
            return True
        except Exception:
            return False

    def load_sys_config(self) -> Dict[str, Any]:
        from core import system_config
        return system_config.load(os.path.join(self.get_config_dir(), "sys_config.json"))

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

    # ================= 用户权限管理 =================


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
                if row and verify_password(password, row[0]):
                    if not row[0].startswith(PREFIX):
                        cursor.execute("UPDATE sys_users SET password=? WHERE username=?", (hash_password(password),username.strip()))
                        conn.commit()
                    return True
                return False
        except Exception as e:
            print(f"[-] 验证用户密码失败: {e}")
            return False

    def needs_password_change(self, username):
        conn = sqlite3.connect(self.db_path)
        try:
            row = conn.execute('SELECT password_reset_required FROM sys_users WHERE username=?',(username,)).fetchone()
            return bool(row and row[0])
        finally:
            conn.close()

    def update_user(self, old_username: str, new_username: str, new_password: str) -> tuple[bool, str]:
        """修改账户的用户名和密码"""
        old_u = old_username.strip()
        new_u = new_username.strip()
        if not new_u:
            return False, "新用户名不能为空"
        if (old_u == "ADMIN" and new_u != "ADMIN") or (new_u.upper() == "ADMIN" and new_u != "ADMIN"):
            return False, "Reserved administrator name"
        if not new_password:
            return False, "新密码不能为空"
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                if old_u != new_u:
                    cursor.execute("SELECT COUNT(*) FROM sys_users WHERE username = ? COLLATE NOCASE", (new_u,))
                    if cursor.fetchone()[0] > 0:
                        return False, f"用户名 '{new_u}' 已存在"
                cursor.execute("""
                    UPDATE sys_users 
                    SET username = ?, password = ?, password_reset_required=0, updated_at = datetime('now', 'localtime')
                    WHERE username = ?
                """, (new_u, hash_password(new_password), old_u))
                if cursor.rowcount != 1:
                    return False, "Account no longer exists"
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
                if cursor.rowcount != 1:
                    return False, "Account no longer exists"
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
        if u.upper() == "ADMIN":
            return False, "Reserved administrator name"
        role_val = "admin" if role in ["admin", "管理员"] else "operator"
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM sys_users WHERE username = ? COLLATE NOCASE", (u,))
                if cursor.fetchone()[0] > 0:
                    return False, f"用户名 '{u}' 已存在"
                cursor.execute("""
                    INSERT INTO sys_users (username, password, role, updated_at)
                    VALUES (?, ?, ?, datetime('now', 'localtime'))
                """, (u, hash_password(password), role_val))
                conn.commit()
                return True, "创建成功"
        except Exception as e:
            return False, f"添加用户失败: {e}"


