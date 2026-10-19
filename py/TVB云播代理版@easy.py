# -*- coding: utf-8 -*-
# QQ群：807916734 @Easy
# TVB云播(com.hktvyb.app) -> dr_py / TVBox Python 爬虫源
#
# 协议: Getapp 方案(MacCMS v10 + /api/vod/ 前缀), 写作风格对齐 getapp3.4.6.py
# 实测结论(2026-09-17, host=app.tuxianimg.com):
#   - 全部接口形如 http://<host>/api/vod/<endpoint>, 请求方式 POST(form-urlencoded)
#   - 成功响应 {"code":0,"msg":"","data":"<AES-128-CBC base64>"}, key == iv == data_key(16字节)
#   - 签名: app-api-verify-time + app-api-verify-sign(=AES(time)); init 免签, 其余接口带签
#   - 首页: init -> type_list[{type_id,type_name,type_extend,recommend_list,filter_type_list}]
#           顶层另有 recommend_list(热门) 与 banner_list
#   - 分类: typeFilterVodList(area/year/type_id/page/sort/lang/class) -> recommend_list
#   - 搜索: searchList(keywords/type_id/page) -> search_list
#   - 详情: vodDetail(vod_id) -> vod + player_source_list + vod_play_url_list[{player_code,urls:[{episode_index,name,url}]}]
#   - 播放: urls[].url 为空时用 vodParse(vod_id+player_source_id+episode_index+scene=0) 换 play_url(带 trial 试看限制)
#
# 使用前提(重要):
#   1) host/key 默认取下方类常量; 亦可在配置软链参数覆盖: host/域名、datakey/数据密钥、ua/用户代理、版本
#   2) 仅保留后缀名是 /api/vod/ 的接口代码, 其余(verify/OCR/登录/VIP)一律不引入
import sys
import json
import time
import base64
import hashlib
from urllib.parse import quote

sys.path.append('..')

try:
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import pad, unpad
except Exception:
    from Cryptodome.Cipher import AES
    from Cryptodome.Util.Padding import pad, unpad

from base.spider import Spider


class Spider(Spider):
    title = "TVB云播"
    lang = "zh"
    searchable = 2
    filterable = 1
    quickSearch = 1

    # ---- 配置 ----
    xurl = "http://app.tuxianimg.com"
    key = "MickwrKuMickwrKu"
    iv = "MickwrKuMickwrKu"
    api_path_prefix = "/api/vod"

    def __init__(self):
        self.header = {
            "User-Agent": "okhttp/4.9.3",
            "App-Os-Type": "android",
            "App-Version-Code": "585",
            "Accept-Encoding": "gzip",
            "Connection": "Keep-Alive",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        }
        self.init_data = {}

    def getName(self):
        return self.title

    # ---------------- 工具 ----------------
    def _norm16(self, s):
        b = s.encode("utf-8")
        if len(b) < 16:
            return b.ljust(16, b"\x00")
        return b[:16]

    def decrypt(self, encrypted_b64):
        key_bytes = self._norm16(self.key)
        iv_bytes = self._norm16(self.iv)
        encrypted = base64.b64decode(encrypted_b64)
        cipher = AES.new(key_bytes, AES.MODE_CBC, iv_bytes)
        decrypted = unpad(cipher.decrypt(encrypted), AES.block_size)
        return decrypted.decode("utf-8")

    def encrypt(self, text):
        key_bytes = self._norm16(self.key)
        iv_bytes = self._norm16(self.iv)
        cipher = AES.new(key_bytes, AES.MODE_CBC, iv_bytes)
        ct = cipher.encrypt(pad(text.encode("utf-8"), AES.block_size))
        return base64.b64encode(ct).decode("utf-8")

    def _generate_api_sign(self, timestamp):
        if not self.key:
            return ""
        key_bytes = self._norm16(self.key)
        iv_bytes = self._norm16(self.iv)
        try:
            cipher = AES.new(key_bytes, AES.MODE_CBC, iv_bytes)
            return base64.b64encode(
                cipher.encrypt(pad(str(timestamp).encode("utf-8"), AES.block_size))
            ).decode("utf-8")
        except Exception:
            return ""

    def _build_api_url(self, endpoint):
        prefix = self.api_path_prefix.strip()
        if not prefix.startswith("/"):
            prefix = "/" + prefix
        if prefix.endswith("/"):
            prefix = prefix.rstrip("/")
        return f"{self.xurl}{prefix}/{endpoint}"

    def _fix_image_url(self, pic):
        if not pic:
            return ""
        if pic.startswith(("http://", "https://")):
            return pic
        if pic.startswith("/"):
            return self.xurl + pic
        return self.xurl + "/" + pic

    def _add_sign_headers(self, headers):
        ts = str(int(time.time()))
        sign = self._generate_api_sign(ts)
        if sign:
            headers["app-api-verify-time"] = ts
            headers["app-api-verify-sign"] = sign
        return headers

    def _post(self, endpoint, data=None):
        url = self._build_api_url(endpoint)
        headers = self._add_sign_headers(self.header.copy())
        try:
            resp = self.post(url, headers=headers, data=data or {}, timeout=15)
            if resp.status_code != 200:
                return None
            j = resp.json()
            if not isinstance(j, dict) or j.get("code") != 0:
                return None
            enc = j.get("data")
            if not enc:
                return j
            return json.loads(self.decrypt(enc))
        except Exception as e:
            print(f"TVB请求失败 [{endpoint}]: {e}")
            return None

    def _post_no_sign(self, endpoint, data=None):
        url = self._build_api_url(endpoint)
        headers = self.header.copy()
        try:
            resp = self.post(url, headers=headers, data=data or {}, timeout=15)
            if resp.status_code != 200:
                return None
            j = resp.json()
            if not isinstance(j, dict) or j.get("code") != 0:
                return None
            enc = j.get("data")
            if not enc:
                return j
            return json.loads(self.decrypt(enc))
        except Exception as e:
            print(f"TVB请求失败 [{endpoint}]: {e}")
            return None

    # ---------------- init ----------------
    def init(self, extend=""):
        if extend:
            try:
                ext = json.loads(extend.strip())
            except Exception:
                ext = {}
            host = ext.get("host") or ext.get("域名")
            if host:
                self.xurl = host.rstrip("/")
            key = ext.get("datakey") or ext.get("数据密钥")
            if key:
                self.key = key
                self.iv = key

        data = self._post_no_sign("init", {})
        if data:
            self.init_data = data

    # ---------------- 首页 ----------------
    def homeContent(self, filter):
        if not self.init_data:
            self.init("")
        data = self.init_data
        result = {"class": [], "filters": {}}

        for t in data.get("type_list") or []:
            tname = t.get("type_name", "")
            if tname in ("全部", "QQ", "juo.one") or "企鹅群" in tname:
                continue
            tid = str(t.get("type_id"))
            result["class"].append({"type_id": tid, "type_name": tname})

            filter_items = []
            for f in t.get("filter_type_list") or []:
                fname = f.get("name")
                values = f.get("list") or []
                if not values:
                    continue
                filter_items.append({
                    "key": fname,
                    "name": {"class": "类型", "area": "地区", "lang": "语言", "year": "年份", "sort": "排序"}.get(fname, fname),
                    "value": [{"n": str(v), "v": str(v)} for v in values],
                })
            if not any(f.get("key") == "sort" for f in filter_items):
                filter_items.append({"key": "sort", "name": "排序", "value": [
                    {"n": "最新", "v": "最新"}, {"n": "最热", "v": "最热"}, {"n": "最赞", "v": "最赞"}]})
            if filter_items:
                result["filters"][tid] = filter_items

        return result

    def homeVideoContent(self):
        if not self.init_data:
            self.init("")
        data = self.init_data
        videos = []
        seen = set()

        def add(v):
            vid = v.get("vod_id")
            if not vid or vid in seen:
                return
            seen.add(vid)
            videos.append({
                "vod_id": vid,
                "vod_name": v.get("vod_name", ""),
                "vod_pic": self._fix_image_url(v.get("vod_pic", "")),
                "vod_remarks": v.get("vod_remarks", ""),
            })

        for item in data.get("banner_list") or []:
            add(item)
        for item in data.get("recommend_list") or []:
            add(item)
        for t in data.get("type_list") or []:
            for item in t.get("recommend_list") or []:
                add(item)
        return {"list": videos}

    def homeVod(self):
        try:
            return self.homeVideoContent()
        except Exception:
            return {"list": []}

    # ---------------- 分类 ----------------
    def categoryContent(self, cid, pg, filter, ext):
        body = {
            "area": ext.get("area", "全部"),
            "year": ext.get("year", "全部"),
            "type_id": cid,
            "page": str(pg),
            "sort": ext.get("sort", "最新"),
            "lang": ext.get("lang", "全部"),
            "class": ext.get("class", "全部"),
        }
        data = self._post("typeFilterVodList", body)
        if not data:
            return {"list": [], "page": pg, "pagecount": 9999, "limit": 90, "total": 999999}

        videos = []
        for item in data.get("recommend_list") or []:
            videos.append({
                "vod_id": item.get("vod_id"),
                "vod_name": item.get("vod_name", ""),
                "vod_pic": self._fix_image_url(item.get("vod_pic", "")),
                "vod_remarks": item.get("vod_remarks", ""),
            })
        total = data.get("total") or 0
        return {"list": videos, "page": pg, "pagecount": 9999, "limit": 90, "total": total or 999999}

    # ---------------- 搜索 ----------------
    def searchContent(self, key, quick, pg=1):
        videos = []
        payload = {"keywords": key, "type_id": "0", "page": str(pg)}
        data = self._post("searchList", payload)
        if data:
            for i in data.get("search_list") or []:
                videos.append({
                    "vod_id": i.get("vod_id"),
                    "vod_name": i.get("vod_name", ""),
                    "vod_pic": self._fix_image_url(i.get("vod_pic", "")),
                    "vod_remarks": (f"{i.get('vod_year', '')} {i.get('vod_class', '')}").strip(),
                })
        return {"list": videos, "page": pg, "pagecount": 9999, "limit": 90, "total": 999999}

    # ---------------- 详情 ----------------
    def detailContent(self, ids):
        did = ids[0] if isinstance(ids, list) else ids
        body = {"vod_id": did}
        data = self._post("vodDetail", body)
        if not data:
            return {"list": []}

        vod = data.get("vod") or {}
        player_info_map = {}
        for p in data.get("player_source_list") or []:
            player_info_map[p.get("player_code")] = p

        order = {p.get("player_code"): p.get("sort", 0) for p in data.get("player_source_list") or []}
        vod_play_url_list = sorted(
            data.get("vod_play_url_list") or [],
            key=lambda it: order.get(it.get("player_code"), 0),
        )

        play_from = []
        play_url = []
        name_count = {}

        for itt in vod_play_url_list:
            player_code = itt.get("player_code", "")
            pinfo = player_info_map.get(player_code, {})
            player_name = pinfo.get("player_name") or player_code

            if any(kw in player_name for kw in ("防走丢", "群", "防失群", "官网")):
                player_name = f"线路{len(name_count) + 1}"

            cnt = name_count.get(player_name, 0) + 1
            name_count[player_name] = cnt
            if cnt > 1:
                player_name = f"{player_name}{cnt}"

            play_from.append(player_name)

            kurls = []
            for idx, it in enumerate(itt.get("urls") or []):
                parse_api = (it.get("parse_api_url") or "").replace(it.get("url") or "", "") or pinfo.get("parse_api") or ""
                play_data = {
                    "url": it.get("url", ""),
                    "parse_api": parse_api,
                    "token": it.get("token", ""),
                    "vod_id": did,
                    "player_source_id": pinfo.get("id", ""),
                    "episode_index": it.get("episode_index") if it.get("episode_index") is not None else idx,
                    "parse_type": str(pinfo.get("player_parse_type", "1")),
                    "user_agent": pinfo.get("user_agent", ""),
                    "headers": pinfo.get("headers") or {},
                }
                kurls.append(f"{it.get('name', '')}${base64.b64encode(json.dumps(play_data, ensure_ascii=False).encode()).decode()}")
            if kurls:
                play_url.append("#".join(kurls))

        return {"list": [{
            "vod_id": did,
            "vod_name": vod.get("vod_name", ""),
            "vod_pic": self._fix_image_url(vod.get("vod_pic", "")),
            "vod_actor": str(vod.get("vod_actor", "")).replace("演员", ""),
            "vod_director": str(vod.get("vod_director", "")).replace("导演", ""),
            "vod_content": vod.get("vod_content", ""),
            "vod_remarks": vod.get("vod_remarks", ""),
            "vod_year": f"{vod['vod_year']}年" if vod.get("vod_year") else "",
            "vod_area": vod.get("vod_area", ""),
            "vod_play_from": "$$$".join(play_from),
            "vod_play_url": "$$$".join(play_url),
        }]}

    # ---------------- 播放 ----------------
    def playerContent(self, flag, id, vipFlags):
        try:
            play_data = json.loads(base64.b64decode(id.encode()).decode())
        except Exception:
            return {"parse": 0, "url": id, "header": {}}

        h = {"User-Agent": play_data.get("user_agent") or "okhttp/4.9.3"}
        pd_headers = play_data.get("headers") or {}
        if isinstance(pd_headers, dict):
            for k, v in pd_headers.items():
                if not v:
                    continue
                if k.lower() == "user-agent" and not play_data.get("user_agent"):
                    h["User-Agent"] = v
                elif k.lower() == "referer":
                    if "Referer" not in h:
                        h["Referer"] = v
                else:
                    h[k] = v

        url = play_data.get("url", "")
        parse_api = play_data.get("parse_api", "")
        token = play_data.get("token", "")
        parse_type = play_data.get("parse_type", "1")
        vod_id = play_data.get("vod_id", "")
        player_source_id = play_data.get("player_source_id", "")
        episode_index = play_data.get("episode_index", 0)

        try:
            # 1) 外置解析
            if parse_api and "url=" in parse_api and parse_api.startswith("http"):
                try:
                    resp = self.get(parse_api + url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10).json()
                    play = resp.get("url")
                    if not play and isinstance(resp.get("data"), dict):
                        play = resp["data"].get("url")
                    if play:
                        return {"parse": 0, "url": play, "header": h}
                except Exception:
                    pass
                return {"parse": 1, "url": parse_api, "header": h}

            # 2) 自带解析器 vodParse
            if parse_api and url:
                body = f"parse_api={quote(parse_api)}&url={quote(self.encrypt(url))}&player_parse_type={parse_type}&token={token}&scene=0"
                resp = self._post("vodParse", body)
                if resp:
                    parsejson = resp
                    play = parsejson.get("url")
                    if not play and "json" in parsejson:
                        try:
                            play = json.loads(parsejson["json"]).get("url")
                        except Exception:
                            pass
                    if play:
                        return {"parse": 0, "url": play, "header": h}
                return {"parse": 1, "url": "", "header": h}

            # 3) url 为空, 用 vodParse 换地址
            if vod_id and not url:
                body = f"vod_id={vod_id}&player_source_id={player_source_id}&episode_index={episode_index}&scene=0"
                resp = self._post("vodParse", body)
                if not resp:
                    return {"parse": 1, "url": "", "header": h}
                trial = resp.get("trial")
                if trial and not trial.get("current_episode_allowed", False):
                    return {"parse": 1, "url": "", "header": h, "msg": "试看限制"}
                play = resp.get("play_url") or resp.get("url")
                if play:
                    return {"parse": 0, "url": play, "header": h}
                return {"parse": 1, "url": "", "header": h}

            # 4) 直链
            if url:
                return {"parse": 0, "url": url, "header": h}
            return {"parse": 1, "url": "", "header": h}
        except Exception as e:
            print(f"TVB播放失败: {e}")
            return {"parse": 1, "url": "", "header": h}

    def isVideoFormat(self, url):
        return any(url.lower().endswith(fmt) for fmt in [".m3u8", ".mp4", ".avi", ".mkv", ".flv", ".ts"])

    def manualVideoCheck(self):
        pass

    def localProxy(self, params):
        if params.get("type") == "m3u8":
            return {
                "url": params.get("url", ""),
                "header": params.get("header", {}),
                "param": params.get("param", ""),
                "type": "m3u8",
            }
        return None


if __name__ == "__main__":
    pass
