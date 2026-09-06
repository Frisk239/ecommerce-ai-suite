"""裸 psycopg 连通检查：本刀无 ORM/迁移，健康检查只回答「数据库可达吗」。"""

import psycopg


def check_database(database_url: str, *, timeout_seconds: float = 3.0) -> bool:
    """执行 SELECT 1 验证连通性。

    任何失败都只返回 False：异常文本（含连接串/密码）一律不向上传播，
    防止经 /health 响应泄露。
    """
    try:
        with psycopg.connect(database_url, connect_timeout=max(1, int(timeout_seconds))) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
    except Exception:  # noqa: BLE001 - 健康检查必须吞掉一切连接错误细节
        return False
    return True
