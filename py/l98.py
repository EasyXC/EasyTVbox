# -*- coding: utf-8 -*-
# QQ群：807916734
# l98.cn 魔法盒子 影视源 (EasyTV 原生兼容 + TVBox/OK影视 CSP + dr_py 双兼容 v4)
# 数据走站点内部接口: /api/site/config /api/site/catalog /api/detail /api/search
# 影视推荐板块对接苹果CMS标准源(api.wsyzy.net)
# 播放: 直链 m3u8, 不依赖第三方解析; playerContent 自动解析 master 取绝对子流地址,
#       避免部分播放器对 master 相对路径子流/异常 BANDWIDTH 解析失败
# 筛选: 懒加载站点 catalog 返回的 filters(按 type_id 组织, 含年代筛选), 失败时用通用硬编码兜底
# 使用方式:
#   EasyTV:      复制到 EasyTV/py/ 目录, 在 easytv.json 注册 {"key":"魔法盒子","type":3,
#                "api":"./py/l98.py","searchable":1,"quickSearch":1,"filterable":1}
#   TVBox/OK影视: 类名 Spider (自动回退 object, 各环境通用)
#   dr_py:       类名 Site (init/home/category/detail/play/search)
import importlib
import json
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request

# EasyTV 引擎运行时提供 base.spider 基类 (源文件位于 EasyTV/py/ 子目录, 父目录即 EasyTV 根)
try:
    sys.path.append('..')
    from base.spider import Spider as _BaseSpider
    _BASE_KIND = 'easytv'
except Exception:
    _BaseSpider = object
    _BASE_KIND = 'fallback'

# 若 EasyTV 基类不可用, 再探测 TVBox/OK影视 的 CSP_Python 基类
if _BASE_KIND == 'fallback':
    try:
        mod = importlib.import_module('csp')
        _b = getattr(mod, 'CSP_Python', None)
        if isinstance(_b, type):
            _BaseSpider = _b
            _BASE_KIND = 'csp'
    except Exception:
        pass

SALT = 'mfys-api-guard-v1'
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

_UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'

_VIDEO_FMTS = ('.m3u8', '.mp4', '.flv', '.avi', '.mkv', '.mov', '.ts', '.mpd', '.webm')


def _fnv1a(value):
    h = 2166136261
    for ch in str(value):
        h ^= ord(ch)
        h = (h * 16777619) & 0xFFFFFFFF
    return h


def _to36(n):
    if n == 0:
        return '0'
    digits = '0123456789abcdefghijklmnopqrstuvwxyz'
    out = ''
    while n:
        out = digits[n % 36] + out
        n //= 36
    return out


def _sign(method, pathname, ts):
    return _to36(_fnv1a('|'.join([SALT, str(method).upper(), pathname, str(ts)])))


# 通用筛选兜底(站点懒加载失败时使用; 站点真实支持年份筛选, 其余参数站点透传/CMS忽略, 不会导致空列表)
_YEARS = ['2026', '2025', '2024', '2023', '2022', '2021', '2020', '2019', '2018', '2017', '2016', '2015', '2014', '2013', '2012', '2011', '2010']
_AREAS = ['大陆', '香港', '台湾', '美国', '韩国', '日本', '英国', '法国', '泰国', '印度', '其他']
_MOVIE_CLASS = ['动作', '喜剧', '爱情', '科幻', '剧情', '悬疑', '恐怖', '战争', '犯罪', '冒险', '记录', '动画', '奇幻', '伦理']
_TV_CLASS = ['古装', '都市', '家庭', '爱情', '悬疑', '科幻', '战争', '历史', '剧情', '喜剧', '动作', '犯罪', '偶像', '其他']
_VARIETY_CLASS = ['真人秀', '脱口秀', '音乐', '访谈', '晚会', '纪实', '竞技', '其他']
_ANIME_CLASS = ['热血', '冒险', '搞笑', '校园', '恋爱', '奇幻', '机战', '运动', '悬疑', '日常', '治愈', '其他']
_ORDERS = [{'n': '最新', 'v': 'time'}, {'n': '最热', 'v': 'hits'}, {'n': '评分', 'v': 'score'}]


def _mk_filter(year_sub, area_ops, class_ops, with_order=True):
    """组装 EasyTV/TVBox filters 结构: [{key,name,value:[{n,v},...]}, ...]"""
    out = [{'key': 'year', 'name': '年代', 'value': [{'n': '全部', 'v': ''}] + [{'n': str(y), 'v': str(y)} for y in year_sub]}]
    if area_ops:
        out.append({'key': 'area', 'name': '地区', 'value': [{'n': '全部', 'v': ''}] + [{'n': a, 'v': a} for a in area_ops]})
    if class_ops:
        out.append({'key': 'class', 'name': '类型', 'value': [{'n': '全部', 'v': ''}] + [{'n': c, 'v': c} for c in class_ops]})
    if with_order:
        out.append({'key': 'order', 'name': '排序', 'value': [{'n': '全部', 'v': ''}] + list(_ORDERS)})
    return out


class Spider(_BaseSpider):
    # ============ 站点配置 ============
    siteUrl = 'https://l98.cn'
    NAME = '魔法盒子'
    HOST = 'https://l98.cn'
    CMS_API = 'https://api.wsyzy.net/api.php/provide/vod'
    SEARCH_API = 'tvbox-py://tv'
    SEARCH_TAG = 'tv_'
    _CONFIG_LOADED = False
    _FILTERS_LOADED = False

    # 首页分类(硬编码兜底, 不依赖网络; 加载 config 成功后可能被刷新)
    classify = [
        {'type_id': '6,7,8,9,10,11,12', 'type_name': '电影'},
        {'type_id': '13,14,15,16,17,18,19,20,21,22,23', 'type_name': '电视剧'},
        {'type_id': '25,26,27,28', 'type_name': '综艺'},
        {'type_id': '29,30,31,44,45', 'type_name': '动漫'},
        {'type_id': '39', 'type_name': '动画片'},
        {'type_id': '54,64,65,66,67,68,69,73', 'type_name': '短剧'},
        {'type_id': '62', 'type_name': '4K电影'},
        {'type_id': '70', 'type_name': '邵氏电影'},
        {'type_id': '71', 'type_name': 'Netflix电影'},
        {'type_id': '72', 'type_name': 'Netflix剧集'},
    ]
    # EasyTV filters 结构: {type_id: [筛选组,...]}, key 必须与 classify 的 type_id 一致
    filters = {}

    def __init__(self):
        self.site_url = self.HOST
        self.headers = {
            'User-Agent': _UA,
            'Referer': self.HOST + '/',
            'Origin': self.HOST,
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'zh-CN,zh;q=0.8',
        }

    # ==================== EasyTV 扩展接口 ====================
    def getName(self) -> str:
        return self.NAME

    def isVideoFormat(self, url: str) -> bool:
        if not url:
            return False
        return any(f in str(url).lower() for f in _VIDEO_FMTS)

    def manualVideoCheck(self) -> bool:
        return False

    def destroy(self):
        pass

    def localProxy(self, param: str = '') -> dict:
        """本地代理: 直链 m3u8 无需代理, 直接返回空占位"""
        result = {'url': '', 'header': '', 'mimeType': ''}
        if param:
            try:
                decoded = urllib.parse.unquote(str(param))
                if decoded.startswith('http'):
                    result['url'] = decoded
                    result['header'] = json.dumps({'User-Agent': _UA, 'Referer': self.HOST + '/'})
                    result['mimeType'] = 'video/mp4'
            except (ValueError, TypeError):
                pass
        return result

    # ==================== 请求封装(带站点 FNV-1a 签名反爬) ====================
    def _request(self, method, path, body=None, timeout=15):
        ts = str(int(time.time() * 1000))
        headers = dict(self.headers)
        headers['X-MF-TS'] = ts
        headers['X-MF-Sign'] = _sign(method, path, ts)
        headers['X-MF-Client'] = 'web'
        data = None
        if body is not None:
            data = json.dumps(body).encode('utf-8')
            headers['Content-Type'] = 'application/json'
        req = urllib.request.Request(self.HOST + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
                return json.loads(resp.read().decode('utf-8', 'replace'))
        except Exception:
            return None

    def _post(self, path, body):
        return self._request('POST', path, body)

    def _get(self, path):
        return self._request('GET', path)

    # ==================== CSP 标准接口 (EasyTV / TVBox / OK影视) ====================
    def init(self, extend=""):
        # EasyTV / TVBox 约定: 返回扩展串(空串即可); 不发起网络请求, 保证任何引擎都能秒返回
        return ""

    def homeContent(self, filter=False):
        if not self._CONFIG_LOADED:
            self._load_config()
        if not self._FILTERS_LOADED:
            self._load_filters()
        result = {}
        result['class'] = self.classify
        result['filters'] = self.filters or {}
        return result

    def homeVideoContent(self):
        """EasyTV 首页推荐: 取影视板块第一页"""
        return self.categoryContent(self.classify[0]['type_id'], 1, False, {})

    def categoryContent(self, tid='', pg=1, filter=None, extend=None):
        if filter is None:
            filter = False
        if extend is None:
            extend = {}
        try:
            page = int(pg or 1)
        except Exception:
            page = 1
        tid = str(tid or '')
        if not self._FILTERS_LOADED:
            self._load_filters()
        # 站点真实支持筛选: extend 原样透传(实测年份筛选生效), 剔除空值
        ext = {}
        if isinstance(extend, dict):
            for k, v in extend.items():
                if v is not None and str(v) != '':
                    ext[str(k)] = v
        result = self._post('/api/site/catalog', {
            'api': self.CMS_API,
            'tid': tid,
            'page': page,
            'extend': ext,
        })
        if not result or not result.get('success'):
            # 站点接口异常时直连苹果CMS兜底
            return self._category_cms_fallback(tid, page, ext)
        data = result.get('data') or {}
        lst = data.get('list') or []
        # 站点返回的 filters 按 tid 缓存(供后续 homeContent 使用, 提升筛选 UI 完整度)
        if data.get('filters') and isinstance(data.get('filters'), dict):
            self._merge_site_filters(data['filters'])
        return {
            'list': lst,
            'page': int(data.get('page') or page),
            'pagecount': int(data.get('pagecount') or 0),
            'limit': int(data.get('limit') or 20),
            'total': int(data.get('total') or 0),
        }

    def _category_cms_fallback(self, tid, page, extend=None):
        """站点接口不可用时直连 CMS 接口: ?ac=detail&t={tid}&pg={page}(CMS 仅支持基础参数)"""
        try:
            query = {'ac': 'detail', 't': tid, 'pg': page}
            if extend and extend.get('year'):
                query['year'] = extend['year']
            if extend and extend.get('order'):
                query['order'] = extend['order']
            url = self.CMS_API + '?' + urllib.parse.urlencode(query)
            req = urllib.request.Request(url, headers=self.headers)
            with urllib.request.urlopen(req, timeout=15, context=_SSL_CTX) as resp:
                raw = resp.read().decode('utf-8', 'replace')
            data = json.loads(raw)
        except Exception:
            return {'list': [], 'page': page, 'pagecount': 0, 'limit': 20, 'total': 0}
        lst = data.get('list') or []
        return {
            'list': lst,
            'page': page,
            'pagecount': int(data.get('pagecount') or 0),
            'limit': int(data.get('limit') or 20),
            'total': int(data.get('total') or 0),
        }

    def detailContent(self, ids=''):
        ids = self._parse_ids(ids)
        if not ids:
            return {'list': []}
        ids = str(ids)
        api = self.CMS_API
        real_id = ids
        if ids.startswith(self.SEARCH_TAG):
            api = self.SEARCH_API
            real_id = ids[len(self.SEARCH_TAG):]
        result = self._post('/api/detail', {'ids': real_id, 'api': api})
        if not result or not result.get('success'):
            return {'list': []}
        vod = result.get('data') or {}
        if not vod or 'vod_id' not in vod:
            return {'list': []}
        if vod.get('vod_content'):
            vod['vod_content'] = re.sub(r'<[^>]+>', '', vod['vod_content']).strip()
        if not vod.get('vod_play_from'):
            vod['vod_play_from'] = '直链'
        if not vod.get('vod_play_url'):
            vod['vod_play_url'] = ''
        if api != self.CMS_API and vod.get('vod_play_url'):
            vod['vod_play_url'] = self._abs_play_url(vod['vod_play_url'])
        return {'list': [vod]}

    def searchContent(self, key='', quick=False, pg=1):
        try:
            page = int(pg or 1)
        except Exception:
            page = 1
        key = str(key or '').strip()
        if not key:
            return {'list': [], 'page': page, 'pagecount': 0, 'limit': 20, 'total': 0}
        items = []
        # 1) 先试影视推荐 CMS 源
        result = self._post('/api/search', {'api': self.CMS_API, 'keyword': key, 'page': page})
        if result and result.get('success'):
            items = result.get('data') or []
            if isinstance(items, dict):
                items = items.get('list') or []
        # 2) CMS 不支持搜索时, 用站点内置 TVBox 源兜底
        if not items:
            result = self._post('/api/search', {'api': self.SEARCH_API, 'keyword': key, 'page': page})
            if result and result.get('success'):
                items = result.get('data') or []
                if isinstance(items, dict):
                    items = items.get('list') or []
            for it in items:
                it['vod_id'] = self.SEARCH_TAG + str(it.get('vod_id', ''))
        return {
            'list': items,
            'page': page,
            'pagecount': page,
            'limit': 20,
            'total': len(items),
        }

    def searchContentPage(self, key='', quick=False, pg=1):
        """搜索分页"""
        return self.searchContent(key, quick, pg)

    def playerContent(self, flag='', id='', vipFlags=''):
        id = str(id or '')
        if '$' in id:
            url = id.split('$', 1)[1]
        else:
            url = id
        if url and url.startswith('/'):
            url = self.HOST + url
        if not url:
            return {'parse': 0, 'playUrl': '', 'url': '', 'header': json.dumps({'User-Agent': _UA})}
        referer = self._pick_referer(url)
        # m3u8: 自动解析 master -> 返回绝对化的子流地址, 规避相对路径/异常 BANDWIDTH 导致播放器解析失败
        url = self._resolve_m3u8(url, referer)
        return {
            'parse': 0,
            'playUrl': '',
            'url': url,
            'header': json.dumps({'User-Agent': _UA, 'Referer': referer}),
        }

    # ==================== 筛选与配置加载 ====================
    def _load_config(self):
        """懒加载站点分类配置; 失败时保留硬编码分类, 单次尝试, 不阻塞"""
        Spider._CONFIG_LOADED = True
        cfg = self._get('/api/site/config')
        if not cfg or not cfg.get('success'):
            return
        try:
            data = cfg.get('data') or {}
            for section in data.get('sections') or []:
                if section.get('kind') == 'cms':
                    cats = section.get('categories') or []
                    if cats:
                        self.classify = [{'type_id': str(c['id']), 'type_name': c['name']} for c in cats]
                    break
        except Exception:
            pass

    def _load_filters(self):
        """懒加载站点 filters: 对每个分类请求一次 catalog, 用 data.filters 覆盖对应 type_id;
        站点未返回/请求失败时使用通用硬编码兜底, 保证筛选 UI 一定可用"""
        Spider._FILTERS_LOADED = True
        got_site = False
        if self.classify:
            for c in self.classify:
                tid = str(c['type_id'])
                r = self._post('/api/site/catalog', {'api': self.CMS_API, 'tid': tid, 'page': 1, 'extend': {}})
                if r and r.get('success'):
                    data = r.get('data') or {}
                    flt = data.get('filters') if isinstance(data.get('filters'), dict) else None
                    if flt:
                        if tid in flt:
                            self.filters[tid] = flt[tid]
                            got_site = True
                        elif flt:
                            # 站点 key 为单分类 id 时取第一个非空组并挂到当前合并 tid 下
                            for k, v in flt.items():
                                if isinstance(v, list) and v:
                                    self.filters[tid] = v
                                    got_site = True
                                    break
        if not got_site:
            # 站点无筛选能力, 用通用兜底, 保证 UI 显示筛选栏
            for c in self.classify:
                tid = str(c['type_id'])
                name = str(c.get('type_name') or '')
                if '剧' in name and '电影' not in name:
                    cls = _TV_CLASS
                elif '综艺' in name:
                    cls = _VARIETY_CLASS
                elif '动漫' in name or '动画' in name:
                    cls = _ANIME_CLASS
                else:
                    cls = _MOVIE_CLASS
                self.filters[tid] = _mk_filter(_YEARS[:13], _AREAS, cls)

    def _merge_site_filters(self, site_filters):
        """把响应侧返回的单分类 filters 并入(按 type_id key 或当前合并 tid 映射)"""
        try:
            if not isinstance(site_filters, dict):
                return
            for key, val in site_filters.items():
                if not isinstance(val, list) or not val:
                    continue
                # key 精确命中 classify 的 type_id 则直接覆盖; 否则挂到"最接近"的合并 tid
                matched = None
                for c in self.classify:
                    if str(c['type_id']) == str(key):
                        matched = str(key)
                        break
                if matched:
                    self.filters[matched] = val
                else:
                    for c in self.classify:
                        tids = [t.strip() for t in str(c['type_id']).split(',') if t.strip()]
                        if str(key) in tids:
                            self.filters[str(c['type_id'])] = val
                            break
        except Exception:
            pass

    # ==================== 播放地址处理 ====================
    @staticmethod
    def _pick_referer(url):
        """按域名选 Referer: l98.cn 代理链路必须带 Referer, 其余域名不强制"""
        low = str(url or '').lower()
        if 'l98.cn' in low or low.startswith('/'):
            return 'https://l98.cn/'
        return ''

    def _fetch_text(self, url, timeout=12):
        headers = {'User-Agent': _UA}
        ref = self._pick_referer(url)
        if ref:
            headers['Referer'] = ref
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
                return resp.read().decode('utf-8', 'replace')
        except Exception:
            return None

    def _resolve_m3u8(self, url, referer='', depth=0):
        """m3u8 解析: master(含 #EXT-X-STREAM-INF) -> 抓取并返回绝对化的子流地址;
        非 m3u8 直链(mp4/flv 等)不抓取原样返回; 解析失败返回原始 url, 不阻塞播放"""
        low = str(url or '').lower()
        if not low.startswith('http'):
            return url
        if not low.endswith('.m3u8'):
            # 疑似 master 代理/播放列表(如 l98 tvbox_ep 无后缀)才尝试抓取, 视频直链一律跳过
            if any(f in low for f in ('.mp4', '.flv', '.mkv', '.avi', '.mov', '.webm', '.ts?', '.mpd')):
                return url
            if 'm3u8' not in low and '/play/' not in low and 'tvbox' not in low and 'media' not in low:
                return url
        if depth > 2:
            return url
        text = self._fetch_text(url)
        if not text:
            return url
        if '#EXT-X-STREAM-INF' not in text:
            return url
        # 取第一条子流(多码率时取第一个即可)
        m = re.search(r'#EXT-X-STREAM-INF[^\n]*\n\s*([^\s#][^\n]*)', text)
        if not m:
            return url
        sub = m.group(1).strip()
        sub = re.sub(r'["\'].*$', '', sub).strip()
        if not sub:
            return url
        if sub.startswith('http'):
            return sub
        if sub.startswith('/'):
            return self.HOST + sub
        # 相对路径, 基于 master URL 所在目录拼接
        base = url.rsplit('/', 1)[0]
        return base + '/' + sub

    # ==================== dr_py 兼容包装 (类名 Site) ====================
    def init(self, extend=""):
        # dr_py 约定: init 返回站点 host
        return self.HOST

    def home(self, filter=False):
        return self.homeContent(filter)

    def category(self, tid, pg, filter, extend):
        return self.categoryContent(tid, pg, filter, extend)

    def detail(self, ids):
        return self.detailContent(ids)

    def search(self, key, pg, filter):
        return self.searchContent(key, False, pg)

    def play(self, flag, id, vipFlags=''):
        return self.playerContent(flag, id, vipFlags)

    # ==================== 内部工具 ====================
    @staticmethod
    def _parse_ids(ids):
        """兼容 '[123]' / '[123,124]' / '123' 等 ids 格式, 返回单个 id 字符串或 None"""
        if ids is None:
            return None
        s = str(ids).strip()
        if not s:
            return None
        if s.startswith('['):
            s = s.strip('[]').strip()
        if not s:
            return None
        return s.split(',')[0].strip()

    def _abs_play_url(self, play_url):
        """TVBox 源内部相对播放地址转绝对地址; 保留多线路/多集分隔结构"""
        out = []
        for route in str(play_url).split('$$$'):
            eps = []
            for ep in route.split('#'):
                if '$' in ep:
                    name, url = ep.split('$', 1)
                else:
                    name, url = '', ep
                if url.startswith('/'):
                    url = self.HOST + url
                eps.append(name + '$' + url)
            out.append('#'.join(eps))
        return '$$$'.join(out)


class Site(Spider):
    """dr_py 入口类: dr_py 加载器识别 Site 类"""
    pass