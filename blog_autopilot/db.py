"""数据库连接管理模块 — PostgreSQL + pgvector"""

import logging
import uuid
from contextlib import contextmanager
from datetime import datetime

import psycopg2
import psycopg2.extras
from psycopg2 import pool
from pgvector.psycopg2 import register_vector

from blog_autopilot.config import DatabaseSettings
from blog_autopilot.constants import (
    ASSOCIATION_RECENCY_WEIGHT,
    ASSOCIATION_RECENCY_WINDOW_DAYS,
    ASSOCIATION_TOP_K,
    RELATION_MEDIUM,
    RELATION_STRONG,
    RELATION_WEAK,
    SURVEY_LOOKBACK_DAYS,
    SURVEY_MAX_SOURCE_ARTICLES,
    SURVEY_MIN_ARTICLES,
    TAG_MATCH_THRESHOLD,
)
from blog_autopilot.exceptions import DatabaseError
from blog_autopilot.models import ArticleRecord, AssociationResult, SeriesRecord, TagSet

logger = logging.getLogger("blog-autopilot")


class Database:
    """PostgreSQL 数据库管理，封装连接池和所有数据库操作"""

    def __init__(self, settings: DatabaseSettings) -> None:
        self._settings = settings
        self._pool: pool.SimpleConnectionPool | None = None

    def _ensure_pool(self) -> pool.SimpleConnectionPool:
        """延迟创建连接池"""
        if self._pool is None:
            try:
                self._pool = pool.SimpleConnectionPool(
                    minconn=1,
                    maxconn=5,
                    dsn=self._settings.get_dsn(),
                )
                # 为连接池中的连接注册向量类型
                conn = self._pool.getconn()
                try:
                    register_vector(conn)
                finally:
                    self._pool.putconn(conn)
                logger.info("数据库连接池创建成功")
            except Exception as e:
                raise DatabaseError(f"数据库连接失败: {e}") from e
        return self._pool

    @contextmanager
    def get_connection(self):
        """从连接池获取连接（上下文管理器，自动归还）"""
        p = self._ensure_pool()
        conn = p.getconn()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            p.putconn(conn)

    def close(self) -> None:
        """关闭连接池"""
        if self._pool is not None:
            self._pool.closeall()
            self._pool = None
            logger.info("数据库连接池已关闭")

    def test_connection(self) -> bool:
        """测试数据库连接"""
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                    return cur.fetchone()[0] == 1
        except Exception as e:
            logger.error(f"数据库连接测试失败: {e}")
            return False

    def execute(self, sql: str, params: tuple = ()) -> None:
        """执行单条 SQL（不返回结果）"""
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, params)
        except DatabaseError:
            raise
        except Exception as e:
            raise DatabaseError(f"SQL 执行失败: {e}") from e

    def fetch_one(self, sql: str, params: tuple = ()) -> dict | None:
        """查询单条记录，返回 dict 或 None"""
        try:
            with self.get_connection() as conn:
                with conn.cursor(
                    cursor_factory=psycopg2.extras.RealDictCursor
                ) as cur:
                    cur.execute(sql, params)
                    row = cur.fetchone()
                    return dict(row) if row else None
        except DatabaseError:
            raise
        except Exception as e:
            raise DatabaseError(f"查询失败: {e}") from e

    def fetch_all(self, sql: str, params: tuple = ()) -> list[dict]:
        """查询多条记录，返回 list[dict]"""
        try:
            with self.get_connection() as conn:
                with conn.cursor(
                    cursor_factory=psycopg2.extras.RealDictCursor
                ) as cur:
                    cur.execute(sql, params)
                    return [dict(row) for row in cur.fetchall()]
        except DatabaseError:
            raise
        except Exception as e:
            raise DatabaseError(f"查询失败: {e}") from e

    # ── DDL：数据库初始化 ──

    def initialize_schema(self) -> None:
        """初始化数据库表结构（幂等，可重复执行）"""
        logger.info("开始初始化数据库 schema...")

        ddl_statements = [
            # 启用 pgvector 扩展
            "CREATE EXTENSION IF NOT EXISTS vector",

            # 文章主表
            """
            CREATE TABLE IF NOT EXISTS articles (
                id              VARCHAR(50) PRIMARY KEY,
                title           VARCHAR(200) NOT NULL,
                tag_magazine    VARCHAR(50) NOT NULL,
                tag_science     VARCHAR(50) NOT NULL,
                tag_topic       VARCHAR(50) NOT NULL,
                tag_content     VARCHAR(100) NOT NULL,
                tg_promo        TEXT NOT NULL,
                embedding       vector(3072) NOT NULL,
                url             VARCHAR(500),
                created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                updated_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            )
            """,

            # 标签复合索引
            """
            CREATE INDEX IF NOT EXISTS idx_articles_tags
            ON articles (tag_magazine, tag_science, tag_topic, tag_content)
            """,

            # 时间索引
            """
            CREATE INDEX IF NOT EXISTS idx_articles_created
            ON articles (created_at DESC)
            """,

            # 更新时间触发器函数
            """
            CREATE OR REPLACE FUNCTION update_timestamp()
            RETURNS TRIGGER AS $$
            BEGIN
                NEW.updated_at = NOW();
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
            """,

            # 触发器（DROP + CREATE 保证幂等）
            """
            DROP TRIGGER IF EXISTS trigger_update_timestamp ON articles
            """,
            """
            CREATE TRIGGER trigger_update_timestamp
            BEFORE UPDATE ON articles
            FOR EACH ROW
            EXECUTE FUNCTION update_timestamp()
            """,

            # ── 文章系列表 ──
            """
            CREATE TABLE IF NOT EXISTS article_series (
                id           VARCHAR(50) PRIMARY KEY,
                title        VARCHAR(300) NOT NULL,
                tag_magazine VARCHAR(50) NOT NULL,
                tag_science  VARCHAR(50) NOT NULL,
                tag_topic    VARCHAR(50) NOT NULL,
                created_at   TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                updated_at   TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            )
            """,

            # articles 表新增系列相关列
            "ALTER TABLE articles ADD COLUMN IF NOT EXISTS series_id VARCHAR(50) REFERENCES article_series(id)",
            "ALTER TABLE articles ADD COLUMN IF NOT EXISTS series_order INT",
            "ALTER TABLE articles ADD COLUMN IF NOT EXISTS wp_post_id INT",

            # 系列成员索引
            """
            CREATE INDEX IF NOT EXISTS idx_articles_series
            ON articles (series_id, series_order)
            WHERE series_id IS NOT NULL
            """,

            # ── 审核日志表 ──
            """
            CREATE TABLE IF NOT EXISTS article_reviews (
                id              SERIAL PRIMARY KEY,
                article_title   VARCHAR(200) NOT NULL,
                consistency     INT NOT NULL,
                readability     INT NOT NULL,
                ai_cliche       INT NOT NULL,
                overall_score   INT NOT NULL,
                verdict         VARCHAR(20) NOT NULL,
                issues_json     TEXT,
                summary         VARCHAR(500),
                category_name   VARCHAR(50),
                created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            )
            """,

            # article_reviews 表新增 factuality 列（事实核查维度）
            "ALTER TABLE article_reviews ADD COLUMN IF NOT EXISTS factuality INT",

            # articles 表新增 source_hash 列（原文指纹去重）
            "ALTER TABLE articles ADD COLUMN IF NOT EXISTS source_hash VARCHAR(64)",

            # source_hash 唯一索引（加速精确去重）
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_articles_source_hash
            ON articles (source_hash)
            WHERE source_hash IS NOT NULL
            """,

            # articles 表新增 summary 列（结构化摘要，用于关联上下文增强）
            "ALTER TABLE articles ADD COLUMN IF NOT EXISTS summary TEXT",

            # 单列标签索引（加速关联查询预过滤）
            """
            CREATE INDEX IF NOT EXISTS idx_articles_tag_magazine
            ON articles (tag_magazine)
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_articles_tag_science
            ON articles (tag_science)
            """,

            # 部分索引：未加入系列的文章（加速 find_recent_similar_articles）
            """
            CREATE INDEX IF NOT EXISTS idx_articles_no_series
            ON articles (tag_magazine, tag_science, tag_topic, created_at DESC)
            WHERE series_id IS NULL
            """,

            # 综述文章记录表（防止重复生成）
            """
            CREATE TABLE IF NOT EXISTS article_surveys (
                id              SERIAL PRIMARY KEY,
                tag_topic       VARCHAR(100) NOT NULL,
                title           VARCHAR(300),
                wp_post_id      INT,
                source_count    INT,
                created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            )
            """,
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_surveys_tag_topic
            ON article_surveys (tag_topic)
            """,
        ]

        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    for stmt in ddl_statements:
                        cur.execute(stmt)

                    # 向量索引需要表中有数据后才能高效创建，
                    # 先检查是否已存在，不存在则创建
                    cur.execute("""
                        SELECT 1 FROM pg_indexes
                        WHERE indexname = 'idx_articles_embedding'
                    """)
                    if not cur.fetchone():
                        try:
                            # 使用 SAVEPOINT 防止索引创建失败导致整个事务回滚
                            cur.execute("SAVEPOINT before_vector_index")
                            # 动态计算 IVFFlat lists 参数
                            cur.execute("SELECT COUNT(*) FROM articles")
                            n = cur.fetchone()[0]
                            lists = max(10, min(1000, int(n ** 0.5))) if n > 0 else 100
                            cur.execute(f"""
                                CREATE INDEX idx_articles_embedding
                                ON articles
                                USING ivfflat (embedding vector_cosine_ops)
                                WITH (lists = {lists})
                            """)
                            cur.execute("RELEASE SAVEPOINT before_vector_index")
                        except Exception:
                            # 回滚到 SAVEPOINT，恢复事务状态
                            cur.execute("ROLLBACK TO SAVEPOINT before_vector_index")
                            # IVFFlat 索引要求表中已有足够数据，
                            # 数据不足时跳过，后续可手动创建
                            logger.warning(
                                "向量索引创建跳过（表中数据不足，"
                                "可在数据导入后手动创建）"
                            )

            logger.info("数据库 schema 初始化完成")
        except DatabaseError:
            raise
        except Exception as e:
            raise DatabaseError(f"Schema 初始化失败: {e}") from e

    # ── CRUD 操作 ──

    @staticmethod
    def _generate_id() -> str:
        """生成唯一文章 ID"""
        return str(uuid.uuid4())[:12]

    def insert_article(
        self,
        record: ArticleRecord,
        series_id: str | None = None,
        series_order: int | None = None,
        wp_post_id: int | None = None,
        source_hash: str | None = None,
    ) -> str:
        """插入新文章记录，返回 article ID"""
        article_id = record.id or self._generate_id()

        sql = """
            INSERT INTO articles
                (id, title, tag_magazine, tag_science, tag_topic, tag_content,
                 tg_promo, embedding, url, series_id, series_order, wp_post_id,
                 source_hash, summary)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        params = (
            article_id,
            record.title,
            record.tags.tag_magazine,
            record.tags.tag_science,
            record.tags.tag_topic,
            record.tags.tag_content,
            record.tg_promo,
            record.embedding,
            record.url,
            series_id,
            series_order,
            wp_post_id,
            source_hash,
            record.summary,
        )

        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, params)
            logger.info(f"文章入库成功: {article_id} - {record.title}")
            return article_id
        except DatabaseError:
            raise
        except Exception as e:
            if "duplicate key" in str(e).lower():
                raise DatabaseError(
                    f"文章 ID 重复: {article_id}"
                ) from e
            raise DatabaseError(f"文章插入失败: {e}") from e

    def get_article(self, article_id: str) -> ArticleRecord | None:
        """查询单篇文章"""
        row = self.fetch_one(
            "SELECT * FROM articles WHERE id = %s", (article_id,)
        )
        return self._row_to_record(row) if row else None

    def get_article_by_url(self, url: str) -> ArticleRecord | None:
        """按 URL 查重"""
        row = self.fetch_one(
            "SELECT * FROM articles WHERE url = %s", (url,)
        )
        return self._row_to_record(row) if row else None

    def count_articles(self) -> int:
        """统计文章总数"""
        row = self.fetch_one("SELECT COUNT(*) as cnt FROM articles")
        return row["cnt"] if row else 0

    # ── 两阶段关联查询 ──

    def find_related_articles(
        self,
        tags: TagSet,
        embedding: list[float],
        exclude_id: str | None = None,
        top_k: int = ASSOCIATION_TOP_K,
    ) -> list[AssociationResult]:
        """
        两阶段关联检索：标签筛选 + 向量精排。

        第一阶段：计算标签匹配数，过滤 < TAG_MATCH_THRESHOLD 的文章
        第二阶段：对候选池按 embedding 余弦相似度排序，返回 Top K
        """
        exclude_id = exclude_id or ""

        sql = """
            WITH candidates AS (
                SELECT
                    id, title, tg_promo, summary, embedding, url, created_at,
                    tag_magazine, tag_science, tag_topic, tag_content,
                    (
                        CASE WHEN tag_magazine = %s THEN 1 ELSE 0 END +
                        CASE WHEN tag_science  = %s THEN 1 ELSE 0 END +
                        CASE WHEN tag_topic    = %s THEN 1 ELSE 0 END +
                        CASE WHEN tag_content  = %s THEN 1 ELSE 0 END
                    ) AS tag_match_count
                FROM articles
                WHERE id != %s
                  AND (tag_magazine = %s OR tag_science = %s)
            )
            SELECT
                id, title, tg_promo, summary, url, created_at,
                tag_magazine, tag_science, tag_topic, tag_content,
                tag_match_count,
                CASE
                    WHEN tag_match_count = 4 THEN %s
                    WHEN tag_match_count = 3 THEN %s
                    WHEN tag_match_count = 2 THEN %s
                END AS relation_level,
                1 - (embedding <=> %s::vector) AS similarity,
                GREATEST(0, 1.0 - EXTRACT(EPOCH FROM (NOW() - COALESCE(created_at, NOW())))
                    / 86400.0 / %s) * %s AS recency_bonus
            FROM candidates
            WHERE tag_match_count >= %s
            ORDER BY (1 - (embedding <=> %s::vector))
                * (1 + GREATEST(0, 1.0 - EXTRACT(EPOCH FROM (NOW() - COALESCE(created_at, NOW())))
                    / 86400.0 / %s) * %s) DESC
            LIMIT %s
        """

        params = (
            tags.tag_magazine,
            tags.tag_science,
            tags.tag_topic,
            tags.tag_content,
            exclude_id,
            tags.tag_magazine,
            tags.tag_science,
            RELATION_STRONG,
            RELATION_MEDIUM,
            RELATION_WEAK,
            str(embedding),
            ASSOCIATION_RECENCY_WINDOW_DAYS,
            ASSOCIATION_RECENCY_WEIGHT,
            TAG_MATCH_THRESHOLD,
            str(embedding),
            ASSOCIATION_RECENCY_WINDOW_DAYS,
            ASSOCIATION_RECENCY_WEIGHT,
            top_k,
        )

        try:
            rows = self.fetch_all(sql, params)
        except Exception as e:
            logger.error(f"关联查询失败: {e}")
            return []

        results = []
        for row in rows:
            article = ArticleRecord(
                id=row["id"],
                title=row["title"],
                tags=TagSet(
                    tag_magazine=row["tag_magazine"],
                    tag_science=row["tag_science"],
                    tag_topic=row["tag_topic"],
                    tag_content=row["tag_content"],
                ),
                tg_promo=row["tg_promo"],
                url=row.get("url"),
                created_at=row.get("created_at"),
                summary=row.get("summary"),
            )
            results.append(AssociationResult(
                article=article,
                tag_match_count=row["tag_match_count"],
                relation_level=row["relation_level"],
                similarity=float(row["similarity"]),
            ))

        logger.info(f"关联查询完成: 找到 {len(results)} 篇相关文章")
        return results

    def find_duplicate(
        self,
        embedding: list[float],
        threshold: float = 0.95,
    ) -> dict | None:
        """
        检查是否存在高度相似的文章（内容去重）。

        返回最相似文章的 {id, title, similarity}，不存在则返回 None。
        """
        sql = """
            SELECT id, title, url,
                   1 - (embedding <=> %s::vector) AS similarity
            FROM articles
            ORDER BY embedding <=> %s::vector
            LIMIT 1
        """
        try:
            row = self.fetch_one(sql, (str(embedding), str(embedding)))
        except Exception as e:
            logger.error(f"去重查询失败: {e}")
            return None

        if row and float(row["similarity"]) >= threshold:
            return row
        return None

    def find_duplicate_by_hash(self, source_hash: str) -> dict | None:
        """
        Level 1 去重：按原文 SHA256 精确匹配。

        返回 {id, title, url} 或 None。
        """
        try:
            return self.fetch_one(
                "SELECT id, title, url FROM articles WHERE source_hash = %s",
                (source_hash,),
            )
        except Exception as e:
            logger.error(f"哈希去重查询失败: {e}")
            return None

    def find_similar_titles(
        self,
        title: str,
        tags: TagSet,
    ) -> dict | None:
        """
        Level 3 去重：标题模糊匹配 + 四级标签完全匹配。

        使用 trigram 相似度（pg_trgm）或退化为 LIKE 前缀匹配。
        返回 {id, title, url} 或 None。
        """
        sql = """
            SELECT id, title, url
            FROM articles
            WHERE tag_magazine = %s
              AND tag_science = %s
              AND tag_topic = %s
              AND tag_content = %s
              AND title = %s
            LIMIT 1
        """
        try:
            return self.fetch_one(sql, (
                tags.tag_magazine, tags.tag_science,
                tags.tag_topic, tags.tag_content,
                title,
            ))
        except Exception as e:
            logger.error(f"标题去重查询失败: {e}")
            return None

    def find_nearest_by_embedding(
        self, embedding: list[float], top_k: int = 5,
    ) -> list[dict]:
        """按 embedding 相似度查找最近邻文章的标签

        Raises:
            DatabaseError: 查询失败时向上抛出，不吞异常
        """
        sql = """
            SELECT tag_magazine, tag_science, tag_topic, tag_content
            FROM articles WHERE embedding IS NOT NULL
            ORDER BY embedding <=> %s::vector LIMIT %s
        """
        return self.fetch_all(sql, (str(embedding), top_k))

    # ── 主题推荐查询 ──

    def fetch_all_tags_with_dates(self) -> list[dict]:
        """获取所有文章的标签和创建时间"""
        return self.fetch_all("""
            SELECT tag_magazine, tag_science, tag_topic, tag_content, created_at
            FROM articles ORDER BY created_at DESC
        """)

    def fetch_recent_titles(self, limit: int = 20) -> list[str]:
        """获取最近 N 篇文章标题"""
        rows = self.fetch_all(
            "SELECT title FROM articles ORDER BY created_at DESC LIMIT %s",
            (limit,),
        )
        return [r["title"] for r in rows]

    def compute_centroid(self) -> list[float] | None:
        """计算所有文章 embedding 的质心向量"""
        row = self.fetch_one(
            "SELECT AVG(embedding) as centroid FROM articles"
        )
        if row and row["centroid"] is not None:
            return list(row["centroid"])
        return None

    def find_frontier_articles(
        self, centroid: list[float], top_n: int = 10
    ) -> list[dict]:
        """找到离质心最远的 N 篇文章（含最近邻相似度）"""
        sql = """
            SELECT f.id, f.title,
                   f.tag_magazine, f.tag_science, f.tag_topic, f.tag_content,
                   f.dist_centroid,
                   nn.nn_similarity
            FROM (
                SELECT id, title, embedding,
                       tag_magazine, tag_science, tag_topic, tag_content,
                       embedding <=> %s::vector AS dist_centroid
                FROM articles
                ORDER BY embedding <=> %s::vector DESC
                LIMIT %s
            ) f
            CROSS JOIN LATERAL (
                SELECT 1 - (a.embedding <=> f.embedding) AS nn_similarity
                FROM articles a
                WHERE a.id != f.id
                ORDER BY a.embedding <=> f.embedding
                LIMIT 1
            ) nn
        """
        return self.fetch_all(
            sql, (str(centroid), str(centroid), top_n)
        )

    # ── 文章系列查询 ──

    def detect_series_candidates(
        self,
        tag_magazine: str,
        tag_science: str,
        tag_topic: str,
    ) -> list[SeriesRecord]:
        """查找匹配 top-3 标签的候选系列"""
        rows = self.fetch_all(
            """
            SELECT id, title, tag_magazine, tag_science, tag_topic, created_at
            FROM article_series
            WHERE tag_magazine = %s AND tag_science = %s AND tag_topic = %s
            """,
            (tag_magazine, tag_science, tag_topic),
        )
        return [
            SeriesRecord(
                id=r["id"], title=r["title"],
                tag_magazine=r["tag_magazine"],
                tag_science=r["tag_science"],
                tag_topic=r["tag_topic"],
                created_at=r.get("created_at"),
            )
            for r in rows
        ]

    def get_series_articles(
        self, series_id: str,
    ) -> list[ArticleRecord]:
        """获取系列中所有文章（按 series_order 排序）"""
        rows = self.fetch_all(
            """
            SELECT id, title, tag_magazine, tag_science, tag_topic, tag_content,
                   tg_promo, embedding, url, created_at, series_order, wp_post_id
            FROM articles
            WHERE series_id = %s
            ORDER BY series_order ASC
            """,
            (series_id,),
        )
        return [self._row_to_record(r) for r in rows]

    def get_series_article_embeddings(
        self, series_id: str,
    ) -> list[list[float]]:
        """获取系列中所有文章的 embedding"""
        rows = self.fetch_all(
            """
            SELECT embedding FROM articles
            WHERE series_id = %s AND embedding IS NOT NULL
            """,
            (series_id,),
        )
        return [list(r["embedding"]) for r in rows if r.get("embedding")]

    def create_series(
        self,
        series_id: str,
        title: str,
        tag_magazine: str,
        tag_science: str,
        tag_topic: str,
    ) -> str:
        """创建新系列，返回 series_id"""
        self.execute(
            """
            INSERT INTO article_series (id, title, tag_magazine, tag_science, tag_topic)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (series_id, title, tag_magazine, tag_science, tag_topic),
        )
        logger.info(f"创建新系列: {series_id} - {title}")
        return series_id

    def add_to_series(
        self,
        article_id: str,
        series_id: str,
        series_order: int,
    ) -> None:
        """将文章加入系列"""
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE articles
                        SET series_id = %s, series_order = %s
                        WHERE id = %s
                        """,
                        (series_id, series_order, article_id),
                    )
        except Exception as e:
            raise DatabaseError(f"加入系列失败: {e}") from e

    def get_wp_post_id(self, article_id: str) -> int | None:
        """获取文章的 WordPress post ID"""
        row = self.fetch_one(
            "SELECT wp_post_id FROM articles WHERE id = %s",
            (article_id,),
        )
        return row["wp_post_id"] if row and row.get("wp_post_id") else None

    def find_recent_similar_articles(
        self,
        tag_magazine: str,
        tag_science: str,
        tag_topic: str,
        embedding: list[float],
        lookback_days: int = 30,
        threshold: float = 0.85,
    ) -> list[dict]:
        """
        查找最近 N 天内同 top-3 标签且相似度高的文章。
        用于判断是否应创建新系列。
        """
        sql = """
            SELECT id, title, url, wp_post_id, series_id, created_at,
                   1 - (embedding <=> %s::vector) AS similarity
            FROM articles
            WHERE tag_magazine = %s AND tag_science = %s AND tag_topic = %s
              AND series_id IS NULL
              AND created_at >= NOW() - %s * INTERVAL '1 day'
            ORDER BY embedding <=> %s::vector
            LIMIT 5
        """
        try:
            rows = self.fetch_all(
                sql,
                (
                    str(embedding), tag_magazine, tag_science, tag_topic,
                    lookback_days, str(embedding),
                ),
            )
            return [r for r in rows if float(r["similarity"]) >= threshold]
        except Exception as e:
            logger.error(f"相似文章查询失败: {e}")
            return []

    # ── 审核记录 ──

    def insert_review(
        self,
        article_title: str,
        review,  # QualityReview
        category_name: str | None = None,
    ) -> None:
        """记录审核结果"""
        import json
        issues_json = json.dumps(
            [
                {
                    "category": i.category,
                    "severity": i.severity,
                    "description": i.description,
                    "suggestion": i.suggestion,
                }
                for i in review.issues
            ],
            ensure_ascii=False,
        ) if review.issues else "[]"

        sql = """
            INSERT INTO article_reviews
                (article_title, consistency, factuality, readability, ai_cliche,
                 overall_score, verdict, issues_json, summary, category_name)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        try:
            self.execute(sql, (
                article_title,
                review.consistency_score,
                review.factuality_score,
                review.readability_score,
                review.ai_cliche_score,
                review.overall_score,
                review.verdict,
                issues_json,
                review.summary,
                category_name,
            ))
        except Exception as e:
            logger.warning(f"审核记录保存失败: {e}")

    # ── 审核分析查询 ──

    def fetch_review_stats(
        self,
        category_name: str | None = None,
        limit: int = 50,
    ) -> dict | None:
        """
        获取审核评分统计（平均分、标准差、样本数）。

        返回 dict: {count, avg_consistency, avg_readability, avg_ai_cliche,
                     avg_overall, std_overall} 或 None（无数据时）
        """
        if category_name:
            sql = """
                SELECT
                    COUNT(*)::int AS count,
                    ROUND(AVG(consistency)::numeric, 1) AS avg_consistency,
                    ROUND(AVG(readability)::numeric, 1) AS avg_readability,
                    ROUND(AVG(ai_cliche)::numeric, 1) AS avg_ai_cliche,
                    ROUND(AVG(overall_score)::numeric, 1) AS avg_overall,
                    ROUND(STDDEV_POP(overall_score)::numeric, 2) AS std_overall
                FROM (
                    SELECT consistency, readability, ai_cliche, overall_score
                    FROM article_reviews
                    WHERE category_name = %s
                    ORDER BY created_at DESC
                    LIMIT %s
                ) sub
            """
            params = (category_name, limit)
        else:
            sql = """
                SELECT
                    COUNT(*)::int AS count,
                    ROUND(AVG(consistency)::numeric, 1) AS avg_consistency,
                    ROUND(AVG(readability)::numeric, 1) AS avg_readability,
                    ROUND(AVG(ai_cliche)::numeric, 1) AS avg_ai_cliche,
                    ROUND(AVG(overall_score)::numeric, 1) AS avg_overall,
                    ROUND(STDDEV_POP(overall_score)::numeric, 2) AS std_overall
                FROM (
                    SELECT consistency, readability, ai_cliche, overall_score
                    FROM article_reviews
                    ORDER BY created_at DESC
                    LIMIT %s
                ) sub
            """
            params = (limit,)

        try:
            row = self.fetch_one(sql, params)
            if row and row.get("count", 0) > 0:
                return row
            return None
        except Exception as e:
            logger.warning(f"审核统计查询失败: {e}")
            return None

    def fetch_high_score_articles(
        self,
        min_score: int = 8,
        category_name: str | None = None,
        limit: int = 3,
    ) -> list[dict]:
        """
        获取高分文章标题和摘要，用作写作正面示例。

        LEFT JOIN articles 表获取文章的结构化摘要（article_summary），
        与审核评语（review_summary）分开返回。

        返回 list[dict]: [{article_title, overall_score, summary,
                           article_summary}]
        """
        if category_name:
            sql = """
                SELECT DISTINCT ON (r.article_title)
                    r.article_title, r.overall_score, r.summary,
                    a.summary AS article_summary
                FROM article_reviews r
                LEFT JOIN articles a ON a.title = r.article_title
                WHERE r.overall_score >= %s AND r.category_name = %s
                    AND r.verdict = 'pass'
                ORDER BY r.article_title, r.overall_score DESC
                LIMIT %s
            """
            params = (min_score, category_name, limit)
        else:
            sql = """
                SELECT DISTINCT ON (r.article_title)
                    r.article_title, r.overall_score, r.summary,
                    a.summary AS article_summary
                FROM article_reviews r
                LEFT JOIN articles a ON a.title = r.article_title
                WHERE r.overall_score >= %s AND r.verdict = 'pass'
                ORDER BY r.article_title, r.overall_score DESC
                LIMIT %s
            """
            params = (min_score, limit)

        try:
            return self.fetch_all(sql, params)
        except Exception as e:
            logger.warning(f"高分文章查询失败: {e}")
            return []

    def fetch_cliche_issues(self, limit: int = 200) -> list[dict]:
        """
        从 article_reviews 提取 ai_cliche 类别的问题描述。

        返回 list[dict]: [{description, severity, category_name}]
        """
        sql = """
            SELECT issues_json, category_name
            FROM article_reviews
            WHERE issues_json IS NOT NULL AND issues_json != '[]'
            ORDER BY created_at DESC
            LIMIT %s
        """
        try:
            rows = self.fetch_all(sql, (limit,))
        except Exception as e:
            logger.warning(f"套话问题查询失败: {e}")
            return []

        import json
        results = []
        for row in rows:
            try:
                issues = json.loads(row["issues_json"])
            except (json.JSONDecodeError, TypeError):
                continue
            for issue in issues:
                if issue.get("category") == "ai_cliche":
                    results.append({
                        "description": issue.get("description", ""),
                        "severity": issue.get("severity", "medium"),
                        "category_name": row.get("category_name"),
                    })
        return results

    # ── 综述文章候选查询 ──

    def find_survey_candidates(
        self,
        min_articles: int = SURVEY_MIN_ARTICLES,
        lookback_days: int = SURVEY_LOOKBACK_DAYS,
    ) -> list[dict]:
        """
        检测可生成综述的文章组：按 (magazine, science, topic) 分组统计。
        返回 [{tag_magazine, tag_science, tag_topic, article_count}]
        """
        sql = """
            SELECT tag_magazine, tag_science, tag_topic,
                   COUNT(*) AS article_count
            FROM articles
            WHERE created_at >= NOW() - %s * INTERVAL '1 day'
            GROUP BY tag_magazine, tag_science, tag_topic
            ORDER BY COUNT(*) DESC
        """
        try:
            return self.fetch_all(sql, (lookback_days,))
        except Exception as e:
            logger.error(f"综述候选查询失败: {e}")
            return []

    def fetch_articles_by_tags(
        self,
        tag_magazine: str,
        tag_science: str,
        tag_topic: str | list[str],
        limit: int = SURVEY_MAX_SOURCE_ARTICLES,
    ) -> list[dict]:
        """获取指定标签的文章，tag_topic 支持单个或多个"""
        if isinstance(tag_topic, list):
            placeholders = ",".join(["%s"] * len(tag_topic))
            sql = f"""
                SELECT id, title, summary, tg_promo, url, created_at
                FROM articles
                WHERE tag_magazine = %s AND tag_science = %s
                  AND tag_topic IN ({placeholders})
                ORDER BY created_at DESC
                LIMIT %s
            """
            params = (tag_magazine, tag_science, *tag_topic, limit)
        else:
            sql = """
                SELECT id, title, summary, tg_promo, url, created_at
                FROM articles
                WHERE tag_magazine = %s AND tag_science = %s AND tag_topic = %s
                ORDER BY created_at DESC
                LIMIT %s
            """
            params = (tag_magazine, tag_science, tag_topic, limit)
        try:
            return self.fetch_all(sql, params)
        except Exception as e:
            logger.error(f"按标签获取文章失败: {e}")
            return []

    def has_survey(self, tag_topic: str) -> bool:
        """检查指定 tag_topic 是否已生成过综述"""
        sql = "SELECT 1 FROM article_surveys WHERE tag_topic = %s LIMIT 1"
        try:
            rows = self.fetch_all(sql, (tag_topic,))
            return len(rows) > 0
        except Exception as e:
            logger.error(f"综述去重查询失败: {e}")
            return False

    def insert_survey(
        self,
        tag_topic: str,
        title: str | None = None,
        wp_post_id: int | None = None,
        source_count: int | None = None,
    ) -> None:
        """记录已生成的综述"""
        sql = """
            INSERT INTO article_surveys (tag_topic, title, wp_post_id, source_count)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (tag_topic) DO NOTHING
        """
        try:
            self.execute(sql, (tag_topic, title, wp_post_id, source_count))
        except Exception as e:
            logger.error(f"综述记录插入失败: {e}")

    # ── 内部辅助 ──

    @staticmethod
    def _row_to_record(row: dict) -> ArticleRecord:
        """将数据库行转换为 ArticleRecord"""
        return ArticleRecord(
            id=row["id"],
            title=row["title"],
            tags=TagSet(
                tag_magazine=row["tag_magazine"],
                tag_science=row["tag_science"],
                tag_topic=row["tag_topic"],
                tag_content=row["tag_content"],
            ),
            tg_promo=row["tg_promo"],
            embedding=row.get("embedding"),
            url=row.get("url"),
            created_at=row.get("created_at"),
            summary=row.get("summary"),
        )
