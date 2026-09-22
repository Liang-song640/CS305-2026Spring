# firewall.py

import json
import os
from dataclasses import dataclass

from os_ken.ofproto import ether, inet


@dataclass(frozen=True)
class FirewallRule:
    src_ip: str = None
    dst_ip: str = None
    proto: str = None
    src_port: object = None
    dst_port: object = None
    action: str = "deny"


class Firewall:
    COOKIE = 0x305F
    PRIORITY = 60000

    PROTO_MAP = {
        None: 0,
        "": 0,
        "*": 0,
        "any": 0,
        "icmp": inet.IPPROTO_ICMP,
        "tcp": inet.IPPROTO_TCP,
        "udp": inet.IPPROTO_UDP,
    }

    def __init__(self, rule_file="firewall_rules.json"):
        self.rule_file = rule_file
        self.rules = self._load_rules(rule_file)
        self.installed = set()

    def _normalize_any(self, value):
        if value is None:
            return None
        if isinstance(value, str) and value.strip().lower() in ["", "*", "any"]:
            return None
        return value

    def _normalize_proto(self, proto):
        proto = self._normalize_any(proto)
        if proto is None:
            return None
        return str(proto).lower()

    def _proto_to_number(self, proto):
        proto = self._normalize_proto(proto)
        return self.PROTO_MAP.get(proto, 0)

    def _normalize_port(self, value):
        value = self._normalize_any(value)
        if value is None:
            return 0
        return int(value)

    def _load_rules(self, rule_file):
        """
        Load firewall rules from firewall_rules.json and return a list of FirewallRule.
        """
        rules = []

        # TODO: read rule_file
        # TODO: parse JSON rules
        # TODO: create FirewallRule objects
        # TODO: append them into rules
        default_json=[
            {
                "src_ip": "192.168.117.2",
                "dst_ip": "192.168.117.3",
                "proto":"icmp",
                "src_port": "*",
                "dst_port": "*",
                "action": "deny"
            },
            {
                "src_ip": "192.168.117.2",
                "dst_ip": "192.168.117.3",
                "proto":"tcp",
                "src_port": "*",
                "dst_port": "80",
                "action": "deny"
            }
        ]
        try:
            if os.path.exists(rule_file):
                with open(rule_file,"r",encoding="utf-8") as f:
                    json_data=json.load(f)
                json_rules = json_data["rules"] if isinstance(json_data, dict) else json_data
            else:
                json_rules=default_json
        except Exception as e:
            json_rules=default_json
        for item in json_rules:
            rule=FirewallRule(
                src_ip=item.get("src_ip"),
                dst_ip=item.get("dst_ip"),
                proto=item.get("proto"),
                src_port=item.get("src_port"),
                dst_port=item.get("dst_port"),
                action=item.get("action","deny")
            )
            rules.append(rule)
        return rules

    def install_rules(self, ofctls):
        """
        Install firewall rules to all switches.
        """
        for dpid, ofctl in ofctls.items():
            for rule in self.rules:

                # TODO: only handle deny rules
                if rule.action != "deny":
                    continue
                # TODO: convert protocol name to protocol number
                proto_num = self._proto_to_number(rule.proto)
                # TODO: normalize source and destination ports
                src_port = self._normalize_port(rule.src_port)
                dst_port = self._normalize_port(rule.dst_port)
                # TODO: skip invalid port rules
                match_dict = {"dl_type": ether.ETH_TYPE_IP}
                sip=self._normalize_any(rule.src_ip)
                dip=self._normalize_any(rule.dst_ip)
                if sip is not None:
                    match_dict["nw_src"] = sip
                if dip is not None:
                    match_dict["nw_dst"] = dip
                if proto_num != 0:
                    match_dict["nw_proto"] = proto_num
                if src_port !=0:
                    match_dict["tp_src"] = src_port
                if dst_port !=0:
                    match_dict["tp_dst"] = dst_port
                # TODO: avoid duplicated flow installation
                install_key = (dpid, id(rule))
                if install_key in self.installed:
                    continue
                # TODO: use ofctl.set_flow() to install a high-priority drop flow
                ofctl.set_flow(cookie=self.COOKIE, priority=self.PRIORITY, actions=[], **match_dict)
                self.installed.add(install_key)
                pass