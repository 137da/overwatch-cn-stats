"""网易大神扫码登录的协议常量。
"""

# ========== 接口地址 ==========
URS_QR = "https://q.reg.163.com/qrcode"            # 网易通行证扫码域
DS = "https://inf.ds.163.com/v1/web"               # 大神业务域（SSO / 绑定列表 / 换 token）
DATAMS = "https://datamsapi.ds.163.com/v1/a19ld5tool"  # 战绩数据域（阶段二用）

# 扫码回调地址（大神与网易CC 共用同一套通行证体系）
CALLBACK = "https://api.cc.163.com/v1/mixteamauth/cookie2LoginToken"

# ========== 业务参数 ==========
PRODUCT = "cc_team"      # 扫码产品标识
APP_KEY_OW = "bn"        # 守望先锋在大神侧的 appKey（bn = battlenet 战网）
DTS = "2026"             # 数据赛年
SERVER = "1"             # 服务器编号（国服固定 1）

# ========== 请求头 ==========
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36"
)
ACCEPT_JSON = "application/json, text/plain, */*"

COMMON_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept-Language": "zh-CN,zh;q=0.9",
}

# ========== Origin / Referer ==========
# 接口有 CORS 校验，必须带来源页地址。两套来源对应不同的业务页，不能混用。
# owCard 页：登录、queryCard、queryCountInfo
ORIGIN_CC = "https://ccact.ds.163.com"
REFERER_CC = (
    "https://ccact.ds.163.com/m/daily/df_homepage/owCard.html"
    "?pageId=home_ow_test&pageName=%E5%AE%88%E6%9C%9B%E5%85%88%E9%94%8B%E9%A6%96%E9%A1%B5"
)
# act 页：customer/*、好友、对局详情、getUserConfig 等其余接口
ORIGIN_ACT = "https://act.ds.163.com"
REFERER_ACT = "https://act.ds.163.com/"

# ========== 客户端类型 ==========
GL_CLIENT_TYPE = "60"    # 接口必需的客户端类型标识

# ========== 请求节流 ==========
# 相邻请求保持最小间隔并加入随机抖动，避免给上游造成突发压力、触发其限流保护。
MIN_INTERVAL_MS = 250
JITTER_MS = 400

# ========== 扫码轮询 ==========
POLL_INTERVAL_SEC = 1.0  # 轮询间隔（网易侧约定每秒一次）
POLL_TIMEOUT_SEC = 300   # 二维码有效期上限，超时放弃（5 分钟）

# 轮询返回码语义
RET_CONFIRMED = "200"                                  # 已扫码并确认
RET_WAITING = "408"                                    # 等待中（未扫 / 已扫未确认）
RET_EXPIRED = frozenset({"401", "402", "403", "411"})  # 二维码失效，需重新出码

# ========== HTTP ==========
HTTP_TIMEOUT_SEC = 20
