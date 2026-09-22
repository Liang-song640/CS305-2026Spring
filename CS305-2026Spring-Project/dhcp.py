from os_ken.lib import addrconv
from os_ken.lib.packet import packet
from os_ken.lib.packet import ethernet
from os_ken.lib.packet import ipv4
from os_ken.lib.packet import udp
from os_ken.lib.packet import dhcp
import time
import struct


class Config():
    controller_macAddr = '7e:49:b3:f0:f9:99'  # don't modify, a dummy mac address for fill the mac enrty
    dns = '8.8.8.8'                           # don't modify, just for the dns entry
    start_ip = '12.0.0.1'                  # can be modified
    end_ip = '12.0.0.255'                  # can be modified
    netmask = '255.0.0.0'                 # can be modified
    # 过期回收演示请用 10
    lease_time = 10

    # You may use above attributes to configure your DHCP server.
    # You can also add more attributes like "lease_time" to support bouns function.


class DHCPServer():
    hardware_addr = Config.controller_macAddr
    start_ip = Config.start_ip
    end_ip = Config.end_ip
    netmask = Config.netmask
    dns = Config.dns
    lease_time = Config.lease_time

    allocated_ips = {}                  # key:mac_addr, value:assigned_ip
    lease_record = {}                   # key:mac_addr, value:lease_expire_timestamp
    current_ip_suffix = int(Config.start_ip.split('.')[-1])
    max_ip_suffix = int(Config.end_ip.split('.')[-1])
    ip_prefix = '.'.join(Config.start_ip.split('.')[:-1]) + '.'

    @classmethod
    def _is_dhcp_renewal(cls, req_dhcp, client_mac):
        ciaddr = getattr(req_dhcp, 'ciaddr', None) or '0.0.0.0'
        if ciaddr in ('0.0.0.0', '0', ''):
            return False
        return cls.allocated_ips.get(client_mac) == ciaddr

    @classmethod
    def _allocate_new_ip(cls, client_mac):
        now = time.time()
        if client_mac in cls.allocated_ips:
            expire_ts = cls.lease_record.get(client_mac, 0)
            if now < expire_ts:
                return cls.allocated_ips[client_mac]

        if cls.current_ip_suffix <= cls.max_ip_suffix:
            allocated = f"{cls.ip_prefix}{cls.current_ip_suffix}"
            cls.current_ip_suffix += 1
            cls.allocated_ips[client_mac] = allocated
            cls.lease_record[client_mac] = now + cls.lease_time
            return allocated

        print(f"[DHCP ERROR] IP Pool exhausted! Cannot allocate IP for MAC: {client_mac}")
        return None

    @classmethod
    def assemble_ack(cls, pkt, datapath, port):
        req_eth = pkt.get_protocol(ethernet.ethernet)
        req_ipv4 = pkt.get_protocol(ipv4.ipv4)
        req_udp = pkt.get_protocol(udp.udp)
        req_dhcp = pkt.get_protocol(dhcp.dhcp)

        client_mac = req_eth.src
        assigned_ip = cls.allocated_ips.get(client_mac)
        if not assigned_ip:
            print(f"[DHCP ERROR] ACK failed: No IP allocated for MAC {client_mac}")
            return None

        if not cls._is_dhcp_renewal(req_dhcp, client_mac):
            cls.lease_record[client_mac] = time.time() + cls.lease_time

        ack_pkt = packet.Packet()
        ack_pkt.add_protocol(ethernet.ethernet(ethertype=0x0800, dst=client_mac, src=cls.hardware_addr))
        ack_pkt.add_protocol(ipv4.ipv4(dst='255.255.255.255', src=assigned_ip, proto=17))
        ack_pkt.add_protocol(udp.udp(dst_port=68, src_port=67))

        lease_bin = struct.pack('!I', cls.lease_time)
        options = dhcp.options(option_list=[
            dhcp.option(tag=53, value=b'\x05'), 
            dhcp.option(tag=1, value=addrconv.ipv4.text_to_bin(cls.netmask)),
            dhcp.option(tag=6, value=addrconv.ipv4.text_to_bin(cls.dns)),
            dhcp.option(tag=51, value=lease_bin), 
            dhcp.option(tag=54, value=addrconv.ipv4.text_to_bin(assigned_ip))
        ])

        ack_pkt.add_protocol(dhcp.dhcp(
            op=2, chaddr=req_dhcp.chaddr, htype=1, hlen=6,
            xid=req_dhcp.xid, yiaddr=assigned_ip, siaddr=assigned_ip,
            options=options
        ))
        return ack_pkt

    @classmethod
    def assemble_offer(cls, pkt, datapath):
        req_eth = pkt.get_protocol(ethernet.ethernet)
        req_ipv4 = pkt.get_protocol(ipv4.ipv4)
        req_udp = pkt.get_protocol(udp.udp)
        req_dhcp = pkt.get_protocol(dhcp.dhcp)

        client_mac = req_eth.src
        assigned_ip = cls._allocate_new_ip(client_mac)
        if not assigned_ip:
            print(f"[DHCP ERROR] OFFER failed: Allocation failed for MAC {client_mac}")
            return None

        offer_pkt = packet.Packet()
        offer_pkt.add_protocol(ethernet.ethernet(ethertype=0x0800, dst=client_mac, src=cls.hardware_addr))
        offer_pkt.add_protocol(ipv4.ipv4(dst='255.255.255.255', src=assigned_ip, proto=17))
        offer_pkt.add_protocol(udp.udp(dst_port=68, src_port=67))

        lease_bin = struct.pack('!I', cls.lease_time)
        options = dhcp.options(option_list=[
            dhcp.option(tag=53, value=b'\x02'),          # OFFER=2
            dhcp.option(tag=1, value=addrconv.ipv4.text_to_bin(cls.netmask)),
            dhcp.option(tag=6, value=addrconv.ipv4.text_to_bin(cls.dns)),
            dhcp.option(tag=51, value=lease_bin),
            dhcp.option(tag=54, value=addrconv.ipv4.text_to_bin(assigned_ip))
        ])

        offer_pkt.add_protocol(dhcp.dhcp(
            op=2, chaddr=req_dhcp.chaddr, htype=1, hlen=6,
            xid=req_dhcp.xid, yiaddr=assigned_ip, siaddr=assigned_ip,
            options=options
        ))
        return offer_pkt

    @classmethod
    def handle_dhcp(cls, datapath, port, pkt):
        dhcp_proto = pkt.get_protocol(dhcp.dhcp)
        if not dhcp_proto:
            print("[DHCP ERROR] Received a non-DHCP packet in handle_dhcp.")
            return

        dhcp_type = None
        for opt in dhcp_proto.options.option_list:
            if opt.tag == 53:
                dhcp_type = opt.value[0]
                break

        if dhcp_type is None:
            print("[DHCP ERROR] DHCP Option 53 (Message Type) missing.")
            return

        if dhcp_type == 1:
            print(f"[DHCP INFO] Received DHCP DISCOVER from port {port}")
            reply_pkt = cls.assemble_offer(pkt, datapath)
            if reply_pkt:
                cls._send_packet(datapath, port, reply_pkt)
                print(f"[DHCP SUCCESS] Sent DHCP OFFER to port {port}")
        elif dhcp_type == 3:
            print(f"[DHCP INFO] Received DHCP REQUEST from port {port}")
            reply_pkt = cls.assemble_ack(pkt, datapath, port)
            if reply_pkt:
                cls._send_packet(datapath, port, reply_pkt)
                print(f"[DHCP SUCCESS] Sent DHCP ACK to port {port}")
        else:
            print(f"[DHCP WARN] Unsupported DHCP Message Type: {dhcp_type}")

    @classmethod
    def _send_packet(cls, datapath, port, pkt):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        pkt.serialize()
        data = pkt.data
        actions = [parser.OFPActionOutput(port=port)]
        out = parser.OFPPacketOut(datapath=datapath,
                                  buffer_id=ofproto.OFP_NO_BUFFER,
                                  in_port=ofproto.OFPP_CONTROLLER,
                                  actions=actions,
                                  data=data)
        datapath.send_msg(out)

    # Bonus：过期租约清理函数（可选定时调用）
    @classmethod
    def clean_expired_lease(cls):
        now = time.time()
        del_mac_list = [
            mac for mac, expire_ts in list(cls.lease_record.items())
            if now >= expire_ts
        ]
        for mac in del_mac_list:
            ip = cls.allocated_ips.pop(mac, None)
            cls.lease_record.pop(mac, None)
            print(f"[DHCP CLEAN] Lease expired, recycle MAC:{mac} IP:{ip}")
        return len(del_mac_list)