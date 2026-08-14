import pymysql
from pymysql.cursors import DictCursor
import os
import hashlib
import secrets

DB_CONFIG = {
    'host': os.environ.get('MYSQL_HOST', '127.0.0.1'),
    'port': int(os.environ.get('MYSQL_PORT', 3306)),
    'user': os.environ.get('MYSQL_USER', 'root'),
    'password': os.environ.get('MYSQL_PASSWORD', '123sss'),
    'database': os.environ.get('MYSQL_DATABASE', 'aoqi_forum'),
    'charset': 'utf8mb4',
    'cursorclass': DictCursor,
    'autocommit': True,
}

ROLE_SUPER_ADMIN = 'super_admin'
ROLE_ADMIN = 'admin'
ROLE_USER = 'user'

ROLE_HIERARCHY = {
    ROLE_SUPER_ADMIN: 3,
    ROLE_ADMIN: 2,
    ROLE_USER: 1,
}

def get_db():
    return pymysql.connect(**DB_CONFIG)

def hash_password(password):
    salt = secrets.token_hex(16)
    hashed = hashlib.sha256((password + salt).encode()).hexdigest()
    return f"{salt}${hashed}"

def verify_password(password, stored):
    if not stored or '$' not in stored:
        return False
    salt, hashed = stored.split('$', 1)
    return hashlib.sha256((password + salt).encode()).hexdigest() == hashed

def init_db():
    config = {k: v for k, v in DB_CONFIG.items() if k != 'database'}
    conn = pymysql.connect(**config)
    try:
        with conn.cursor() as cursor:
            cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{DB_CONFIG['database']}` DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
        conn.commit()
    finally:
        conn.close()

    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    username VARCHAR(64) NOT NULL UNIQUE,
                    password_hash VARCHAR(255) NOT NULL,
                    role VARCHAR(32) NOT NULL DEFAULT 'user',
                    avatar VARCHAR(255) NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    INDEX idx_username (username),
                    INDEX idx_role (role)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            ''')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS posts (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    user_id INT NULL DEFAULT NULL,
                    title VARCHAR(255) NOT NULL,
                    content TEXT NOT NULL,
                    author VARCHAR(64) NOT NULL DEFAULT '匿名用户',
                    category VARCHAR(32) NOT NULL DEFAULT '综合讨论',
                    likes INT NOT NULL DEFAULT 0,
                    views INT NOT NULL DEFAULT 0,
                    comment_count INT NOT NULL DEFAULT 0,
                    images JSON NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    INDEX idx_category (category),
                    INDEX idx_created_at (created_at DESC),
                    INDEX idx_likes (likes DESC),
                    INDEX idx_user_id (user_id),
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            ''')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS comments (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    post_id INT NOT NULL,
                    user_id INT NULL DEFAULT NULL,
                    content TEXT NOT NULL,
                    author VARCHAR(64) NOT NULL DEFAULT '匿名用户',
                    likes INT NOT NULL DEFAULT 0,
                    images JSON NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_post_id (post_id),
                    INDEX idx_created_at (created_at DESC),
                    INDEX idx_user_id (user_id),
                    FOREIGN KEY (post_id) REFERENCES posts(id) ON DELETE CASCADE,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            ''')

            try:
                cursor.execute("SELECT images FROM posts LIMIT 1")
            except pymysql.Error:
                cursor.execute("ALTER TABLE posts ADD COLUMN images JSON NULL")
            
            try:
                cursor.execute("SELECT images FROM comments LIMIT 1")
            except pymysql.Error:
                cursor.execute("ALTER TABLE comments ADD COLUMN images JSON NULL")

            try:
                cursor.execute("SELECT user_id FROM posts LIMIT 1")
            except pymysql.Error:
                cursor.execute("ALTER TABLE posts ADD COLUMN user_id INT NULL DEFAULT NULL, ADD INDEX idx_user_id (user_id)")
            
            try:
                cursor.execute("SELECT user_id FROM comments LIMIT 1")
            except pymysql.Error:
                cursor.execute("ALTER TABLE comments ADD COLUMN user_id INT NULL DEFAULT NULL, ADD INDEX idx_user_id (user_id)")

            # 给 posts 添加置顶和精华字段
            try:
                cursor.execute("SELECT is_pinned FROM posts LIMIT 1")
            except pymysql.Error:
                cursor.execute("ALTER TABLE posts ADD COLUMN is_pinned TINYINT NOT NULL DEFAULT 0, ADD COLUMN is_featured TINYINT NOT NULL DEFAULT 0")

            # 给 comments 添加 parent_id 支持楼层回复
            try:
                cursor.execute("SELECT parent_id FROM comments LIMIT 1")
            except pymysql.Error:
                cursor.execute("ALTER TABLE comments ADD COLUMN parent_id INT NULL DEFAULT NULL")

            # 帖子收藏表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS post_favorites (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    user_id INT NOT NULL,
                    post_id INT NOT NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE KEY uk_user_post (user_id, post_id),
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY (post_id) REFERENCES posts(id) ON DELETE CASCADE
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            ''')

            # 通知表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS notifications (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    user_id INT NOT NULL,
                    type VARCHAR(32) NOT NULL,
                    title VARCHAR(255) NOT NULL,
                    content TEXT,
                    link VARCHAR(512),
                    is_read TINYINT NOT NULL DEFAULT 0,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_user_id (user_id),
                    INDEX idx_is_read (is_read),
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            ''')

            # 操作日志表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS audit_logs (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    user_id INT,
                    username VARCHAR(64),
                    action VARCHAR(64) NOT NULL,
                    target_type VARCHAR(32),
                    target_id INT,
                    detail TEXT,
                    ip_address VARCHAR(45),
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_user_id (user_id),
                    INDEX idx_action (action),
                    INDEX idx_created_at (created_at DESC)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            ''')

            # 视频评论表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS video_comments (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    race_id INT NOT NULL,
                    video_id VARCHAR(64) NOT NULL,
                    user_id INT NULL DEFAULT NULL,
                    content TEXT NOT NULL,
                    author VARCHAR(64) NOT NULL DEFAULT '匿名用户',
                    likes INT NOT NULL DEFAULT 0,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_race_video (race_id, video_id),
                    INDEX idx_user_id (user_id),
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            ''')

            cursor.execute("SELECT COUNT(*) as cnt FROM users WHERE role = %s", (ROLE_SUPER_ADMIN,))
            if cursor.fetchone()['cnt'] == 0:
                admin_pwd = hash_password('admin123')
                cursor.execute(
                    "INSERT INTO users (username, password_hash, role) VALUES (%s, %s, %s)",
                    ('admin', admin_pwd, ROLE_SUPER_ADMIN)
                )
        conn.commit()
    finally:
        conn.close()
