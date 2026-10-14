"""Provider presentation and read-only validation capabilities."""
import re

from .share115 import Share115Client, Share115Error, INVALID_SHARE_ERRNOS
from .share123 import Share123Client, Share123Error
from .share_guangya import ShareGuangyaClient, ShareGuangyaError

PROVIDERS = {
    '115': ('115 网盘', '#287db0', Share115Client),
    '123': ('123 网盘', '#476fe6', Share123Client),
    'guangya': ('光鸭云盘', '#ca9513', ShareGuangyaClient),
    'aliyun': ('阿里云盘', '#7761ce', None),
    'quark': ('夸克网盘', '#3874e8', None),
    'tianyi': ('天翼云盘', '#e7772c', None),
    'pikpak': ('PikPak', '#526de0', None),
    'ed2k': ('ED2K', '#7b8791', None),
}


def provider_info(provider):
    label, color, checker = PROVIDERS.get(provider, (provider or '未知来源', '#7b8791', None))
    return {'id': provider, 'label': label, 'color': color, 'icon': 'file' if provider == 'ed2k' else 'cloud', 'can_check': checker is not None}


def probe(provider, url):
    factory = PROVIDERS.get(provider, (None, None, None))[2]
    if factory is None:
        raise ValueError('该来源暂不支持在线检查。')
    try:
        factory().probe_share(url)
        return 'available', None
    except Share115Error as exc:
        if not exc.risk_control and exc.errno in INVALID_SHARE_ERRNOS:
            return 'invalid', '分享已取消或不存在。'
        # Password failures are not proof that a share is gone, so only an
        # explicit dead-share message marks the source invalid.
        if not exc.risk_control and exc.terminal and re.search(r'已失效|已过期|不存在|已取消', str(exc)) and not re.search(r'密码|提取码', str(exc)):
            return 'invalid', '分享已取消、过期或不存在。'
        if re.search(r'密码|提取码', str(exc)):
            return 'pending', '访问码错误，分享可能仍然存在，请核对访问码后人工处理。'
        return 'pending', '访问受限或暂时无法验证，请稍后人工检查。'
    except (Share123Error, ShareGuangyaError) as exc:
        # Password / authorization failures are not proof that a share is gone.
        if exc.terminal and not exc.risk_control and re.search(r'已失效|已过期|不存在|已取消', str(exc)) and not re.search(r'密码|提取码', str(exc)):
            return 'invalid', '分享已取消、过期或不存在。'
        return 'pending', '访问受限、访问码错误或暂时无法验证，请稍后人工检查。'
    except (OSError, TimeoutError):
        return 'pending', '网络暂时不可用，尚未确认分享状态。'
