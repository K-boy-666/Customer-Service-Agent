"""
CS Agent MCP Server — 客服智能体 MCP 服务端
暴露 5 个工具给 Claude Code 调用，绕过 Agent 工具的 reasoning_effort 限制。
每个工具模拟对应子代理的后端数据。
"""

import asyncio
import json
import hashlib
from datetime import datetime, timedelta
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

app = Server("cs-agent-server")

# ── 模拟数据生成 ───────────────────────────────────────────

def _hash_mod(s: str, n: int) -> int:
    """用 SHA256 将字符串映射到 [0, n) 的稳定整数"""
    h = hashlib.sha256(s.encode()).hexdigest()
    return int(h, 16) % n

# 场景库：按订单号 hash 选取，保证同一订单号返回一致数据
PRODUCTS = [
    {"name": "漫步者 X3 真无线蓝牙耳机", "price": 269.00},
    {"name": "小米充电宝 20000mAh", "price": 129.00},
    {"name": "Type-C 快充数据线 1m", "price": 19.90},
    {"name": "iPhone 15 液态硅胶手机壳", "price": 39.00},
    {"name": "罗技 MX Master 3S 鼠标", "price": 499.00},
]

ORDER_STATUSES = [
    {"status": "待付款", "code": "PENDING_PAYMENT", "canModify": True,  "canCancel": True},
    {"status": "已付款", "code": "PAID",            "canModify": True,  "canCancel": True},
    {"status": "配货中", "code": "PREPARING",        "canModify": True,  "canCancel": True},
    {"status": "已发货", "code": "SHIPPED",           "canModify": False, "canCancel": False},
    {"status": "已签收", "code": "DELIVERED",         "canModify": False, "canCancel": False},
    {"status": "已完成", "code": "COMPLETED",         "canModify": False, "canCancel": False},
]

CITIES = ["北京市海淀区***", "上海市浦东新区***", "广州市天河区***", "深圳市南山区***", "杭州市西湖区***"]

CARRIERS = ["顺丰速运", "中通快递", "圆通速递", "韵达快递", "京东物流"]
CARRIER_CODES = ["SF", "ZTO", "YTO", "YUNDA", "JD"]

LOGISTICS_ROUTES = [
    {"from": "深圳", "via": "武汉", "to": "北京"},
    {"from": "广州", "via": "长沙", "to": "上海"},
    {"from": "杭州", "via": "合肥", "to": "北京"},
    {"from": "上海", "via": "南京", "to": "广州"},
]

FAQ_ANSWERS = {
    "退货|不满意|不喜欢|不想要|退吗|能退|可以退": {
        "answer": "亲，本店支持七天无理由退货哦～您收到商品后7天内，只要商品及包装完好、配件齐全、不影响二次销售，就可以在订单页面在线申请退货退款呢。非质量问题退货的话，退回运费需要您自己承担哦；如果是质量问题，运费由我们承担的哈～退款会在仓库收到退货后1-3个工作日内原路返回到您的支付账户呢。",
        "related": ["退货流程怎么操作？", "退货运费谁承担？", "退款多久到账？"],
    },
    "退款|退钱|到账|退回来|退费": {
        "answer": "亲，退款进度取决于售后类型哦～仅退款的话审核通过后1-2个工作日到账；退货退款需要在仓库签收退货后1-3个工作日到账。款项会原路返回到您的支付账户呢。如果超过时效还没收到，请联系人工客服帮您加急处理哈～",
        "related": ["怎么申请退款？", "退款退到哪里？", "超过时效怎么办？"],
    },
    "发货|多久到|什么时候到|配送时效|送达": {
        "answer": "亲，一般情况下，下单后24小时内发货哦～配送时效根据地区不同：一线城市通常2-3天，二三线城市3-5天，偏远地区5-7天呢。您可以在订单详情页查看物流实时轨迹哈～",
        "related": ["怎么查物流？", "能加急配送吗？", "超时没收到怎么办？"],
    },
    "运费|包邮|邮费|免邮": {
        "answer": "亲，本店满99元全国包邮哦～不满99元的话会根据收货地址收取6-12元运费呢。港澳台及海外地区运费需要单独咨询客服哈。退货的非质量问题运费需要您自理，质量问题我们承担来回运费的～",
        "related": ["满多少包邮？", "退货邮费谁出？", "海外能寄吗？"],
    },
    "优惠券|优惠|折扣|满减|领券|红包": {
        "answer": "亲，优惠券可以在首页领券中心领取哦～新人可领满99减15的专属优惠，老用户每月有满199减30的会员券呢。优惠券一般有效期7天，过期就作废啦，记得及时使用哈。注意同一订单只能用一张优惠券，不能叠加使用的～",
        "related": ["怎么领券？", "优惠券能叠加吗？", "券过期了能补吗？"],
    },
    "会员|积分|等级|VIP|权益": {
        "answer": "亲，我们的会员分为普通会员、银卡会员、金卡会员三个等级哦～消费1元积1分，积分可在积分商城兑换商品或抵扣现金（100分=1元）。金卡会员享专属95折、优先售后、生日礼包等权益呢。升级方式就是多多消费啦～",
        "related": ["积分怎么用？", "会员等级怎么升？", "生日礼包是什么？"],
    },
    "支付|付款|分期|花呗|信用卡|白条|发票": {
        "answer": "亲，我们支持微信支付、支付宝、银行卡、花呗分期、京东白条等多种支付方式哦～订单满500元支持花呗3/6/12期免息分期呢。需要发票的话，下单时在备注栏填写发票抬头和税号即可，电子发票会在确认收货后自动发送到您的邮箱哈～",
        "related": ["支持哪些支付方式？", "怎么开发票？", "分期有利息吗？"],
    },
    "保修|维修|坏了|质量问题|故障|售后期限": {
        "answer": "亲，本店所有电子产品享有一年全国联保哦～保修期内出现非人为质量问题，我们可以免费维修或换新呢。人为损坏（摔坏、进水等）不在保修范围内，但可以付费维修哈。超过保修期的也可以联系我们评估维修方案和费用～",
        "related": ["保修多久？", "怎么申请保修？", "人为损坏能修吗？"],
    },
}


def _find_faq(question: str) -> dict:
    """根据问题关键词匹配 FAQ 答案"""
    for pattern, data in FAQ_ANSWERS.items():
        import re
        if re.search(pattern, question):
            return data
    # 默认：通用回复
    return {
        "answer": "亲，感谢您的咨询呢～您的问题小客服已经记录下来了。为了给您更准确的答复，请问能再具体描述一下您想了解的内容吗？比如订单号、商品名或者具体的问题类型，小客服帮您精准查询哈～",
        "related": ["如何联系人工客服？", "客服工作时间是什么？"],
    }


def mock_order(order_id: str) -> dict:
    if not order_id.strip():
        return {"success": False, "data": {}, "error": "缺少订单号，无法查询。请提供您的订单号呢～"}

    n = _hash_mod(order_id, len(PRODUCTS))
    s = _hash_mod(order_id, len(ORDER_STATUSES))
    c = _hash_mod(order_id, len(CITIES))
    product = PRODUCTS[n]
    status_info = ORDER_STATUSES[s]
    qty = (n % 3) + 1
    days_ago = (s + 1) * 1.5

    return {
        "success": True,
        "data": {
            "orderId": order_id,
            "status": status_info["status"],
            "statusCode": status_info["code"],
            "items": [{"name": product["name"], "quantity": qty, "price": product["price"]}],
            "totalAmount": round(product["price"] * qty, 2),
            "createdAt": (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M"),
            "shippingAddress": CITIES[c],
            "canModify": status_info["canModify"],
            "canCancel": status_info["canCancel"],
            "estimateDelivery": (datetime.now() + timedelta(days=3 - s % 3)).strftime("%Y-%m-%d"),
            "notes": "",
        },
        "error": "",
    }


def mock_logistics(order_id: str) -> dict:
    if not order_id.strip():
        return {"success": False, "data": {}, "error": "缺少订单号，无法查询物流呢～"}

    r = _hash_mod(order_id, len(LOGISTICS_ROUTES))
    c = _hash_mod(order_id, len(CARRIERS))
    route = LOGISTICS_ROUTES[r]
    carrier = CARRIERS[c]
    code = CARRIER_CODES[c]

    now = datetime.now()
    tracking = f"{code}{13892746501 + _hash_mod(order_id, 90000):0>5d}"

    history = [
        {"time": (now - timedelta(hours=48)).strftime("%m-%d %H:%M"), "status": "揽收", "location": f"{route['from']}{['龙华','白云','萧山','浦东'][_hash_mod(order_id, 4)]}"},
        {"time": (now - timedelta(hours=36)).strftime("%m-%d %H:%M"), "status": f"离开{route['from']}分拨中心", "location": route['from']},
        {"time": (now - timedelta(hours=12)).strftime("%m-%d %H:%M"), "status": f"到达{route['via']}中转站", "location": route['via']},
    ]

    return {
        "success": True,
        "data": {
            "orderId": order_id,
            "trackingNumber": tracking,
            "carrier": carrier,
            "status": "运输中",
            "statusCode": "IN_TRANSIT",
            "currentLocation": f"{route['via']}中转站",
            "history": history,
            "estimatedDelivery": (now + timedelta(days=2)).strftime("%Y-%m-%d"),
            "isDelayed": False,
            "recipient": "",
            "notes": "",
        },
        "error": "",
    }


def mock_refund(order_id: str) -> dict:
    if not order_id.strip():
        return {"success": False, "data": {}, "error": "缺少订单号，无法查询退款信息呢～"}

    n = _hash_mod(order_id, 6)
    statuses = [
        {"status": "审核中", "code": "PENDING", "type": "RETURN_REFUND"},
        {"status": "已通过", "code": "APPROVED", "type": "REFUND_ONLY"},
        {"status": "待寄回", "code": "WAITING_RETURN", "type": "RETURN_REFUND"},
        {"status": "仓库已签收", "code": "RECEIVED", "type": "RETURN_REFUND"},
        {"status": "退款中", "code": "REFUNDING", "type": "REFUND_ONLY"},
        {"status": "已到账", "code": "COMPLETED", "type": "REFUND_ONLY"},
    ]
    status_info = statuses[n]
    product = PRODUCTS[_hash_mod(order_id, len(PRODUCTS))]
    amount = round(product["price"] * ((_hash_mod(order_id, 3)) + 1), 2)

    return {
        "success": True,
        "data": {
            "refundId": f"RF{datetime.now().strftime('%Y%m%d')}{_hash_mod(order_id, 9000):0>4d}",
            "orderId": order_id,
            "type": status_info["type"],
            "status": status_info["status"],
            "statusCode": status_info["code"],
            "amount": amount,
            "reason": "不满意" if n % 2 == 0 else "商品与描述不符",
            "method": "原路返回",
            "appliedAt": (datetime.now() - timedelta(days=n + 1)).strftime("%Y-%m-%d %H:%M"),
            "estimatedTime": "1-3个工作日" if n < 4 else "已到账",
            "returnAddress": "广东省深圳市龙华区仓储中心 3号仓 518000",
            "notes": "",
        },
        "error": "",
    }


def mock_faq(question: str, category: str = "") -> dict:
    if not question.strip():
        return {"success": False, "data": {}, "error": "请问您想了解哪方面的信息呢？"}

    faq = _find_faq(question)
    return {
        "success": True,
        "data": {
            "question": question[:80],
            "category": category or "OTHER",
            "answer": faq["answer"],
            "relatedQuestions": faq.get("related", []),
            "notes": "",
        },
        "error": "",
    }


def mock_create_ticket(original_question: str, failed_agents: list,
                       partial_results: str, attempted_actions: str,
                       priority: str) -> dict:
    import random
    ticket_id = f"TK-{datetime.now().strftime('%Y%m%d')}-{random.randint(1000, 9999)}"

    priority_label = {"urgent": "🔥 加急", "normal": "📋 普通", "low": "📝 一般"}
    wait_time = {"urgent": "1-2分钟", "normal": "3-5分钟", "low": "5-10分钟"}

    user_message = (
        f"亲，非常抱歉给您带来不便呢～\n\n"
        f"小客服已经认真记录了您的问题，但由于系统限制暂时无法完全处理，正在为您转接人工客服哦。\n\n"
        f"📋 工单编号：{ticket_id}\n"
        f"⏱️ 预计等待：{wait_time.get(priority, '3-5分钟')}\n"
        f"🏷️ 优先级：{priority_label.get(priority, '📋 普通')}\n\n"
        f"人工客服马上来接您，请稍等片刻呢～感谢您的耐心与理解！💕"
    )

    return {
        "success": True,
        "data": {
            "ticketId": ticket_id,
            "priority": priority,
            "summary": original_question[:50] if original_question else "用户咨询",
            "category": "OTHER",
            "userMessage": user_message,
            "collectedContext": {
                "originalQuestion": original_question,
                "failedAgents": failed_agents,
                "partialResults": partial_results,
                "attemptedActions": attempted_actions,
            },
            "estimatedWait": wait_time.get(priority, "3-5分钟"),
            "notes": "",
        },
        "error": "",
    }


# ── MCP 工具注册 ────────────────────────────────────────────

@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="query_order",
            description="查询订单状态。返回订单当前状态、商品明细、金额、收货地址、是否可取消/修改地址。当用户提供订单号并询问订单相关问题时调用。",
            inputSchema={
                "type": "object",
                "properties": {
                    "orderId": {"type": "string", "description": "用户提供的订单号"}
                },
                "required": ["orderId"],
            },
        ),
        Tool(
            name="query_logistics",
            description="查询物流轨迹。返回包裹当前位置、运输历史、承运商、预计送达时间、是否延误。当用户询问快递/物流/配送/到哪了时调用。",
            inputSchema={
                "type": "object",
                "properties": {
                    "orderId": {"type": "string", "description": "关联的订单号"}
                },
                "required": ["orderId"],
            },
        ),
        Tool(
            name="query_refund",
            description="查询退款/售后进度。返回退款状态、金额、预计到账时间、退货地址（如需退货）。当用户询问退款/退货/售后/到账时调用。",
            inputSchema={
                "type": "object",
                "properties": {
                    "orderId": {"type": "string", "description": "关联的订单号"}
                },
                "required": ["orderId"],
            },
        ),
        Tool(
            name="query_faq",
            description="查询平台政策和常见问题。覆盖：退换货规则、会员权益、支付方式、优惠券、配送政策、保修售后。当用户咨询政策/规则/能不能/怎么操作/多久/条件时调用。",
            inputSchema={
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "用户的问题原文或摘要"},
                    "category": {
                        "type": "string",
                        "description": "问题类别：POLICY|MEMBERSHIP|PAYMENT|COUPON|DELIVERY|AFTERSALE|ACCOUNT|OTHER",
                    },
                },
                "required": ["question"],
            },
        ),
        Tool(
            name="create_ticket",
            description="创建转人工工单。当其他工具无法解答用户问题、用户明确要求转人工、或用户投诉/情绪激动时调用。生成工单并返回安抚话术。",
            inputSchema={
                "type": "object",
                "properties": {
                    "originalQuestion": {"type": "string", "description": "用户的原始问题全文"},
                    "failedAgents": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "失败的子代理列表，如 [\"order\", \"logistics\"]",
                    },
                    "partialResults": {"type": "string", "description": "已获取的部分信息"},
                    "attemptedActions": {"type": "string", "description": "已尝试的处理步骤"},
                    "priority": {
                        "type": "string",
                        "description": "优先级：urgent（退款/物流异常/投诉）、normal（一般查询失败）、low（闲聊）",
                    },
                },
                "required": ["originalQuestion"],
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    handlers = {
        "query_order": lambda: mock_order(arguments.get("orderId", "")),
        "query_logistics": lambda: mock_logistics(arguments.get("orderId", "")),
        "query_refund": lambda: mock_refund(arguments.get("orderId", "")),
        "query_faq": lambda: mock_faq(
            arguments.get("question", ""), arguments.get("category", "")
        ),
        "create_ticket": lambda: mock_create_ticket(
            arguments.get("originalQuestion", ""),
            arguments.get("failedAgents", []),
            arguments.get("partialResults", ""),
            arguments.get("attemptedActions", ""),
            arguments.get("priority", "normal"),
        ),
    }

    handler = handlers.get(name)
    if handler:
        result = handler()
    else:
        result = {"success": False, "error": f"未知工具: {name}"}

    return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]


# ── 入口 ────────────────────────────────────────────────────

async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
