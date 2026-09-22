from os_ken.base import app_manager
from os_ken.controller import ofp_event
from os_ken.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER
from os_ken.controller.handler import set_ev_cls
from os_ken.topology import event
from os_ken.topology import api as topo_api
from os_ken.topology.switches import Switch, Host, HostState, Port, PortState, PortData, PortDataState, Link, LinkState
from os_ken.topology.switches import Switches
from os_ken.ofproto import ofproto_v1_0, ether, inet
from os_ken.lib.packet import packet, ethernet, ether_types, arp
from os_ken.lib.packet import dhcp
from os_ken.lib.packet import ethernet
from os_ken.lib.packet import ipv4
from os_ken.lib.packet import packet
from os_ken.lib.packet import udp
from dhcp import DHCPServer
from os_ken.lib import hub
from collections import defaultdict, deque
import os
import time
from ofctl_utilis import OfCtl, OfCtl_v1_0, OfCtl_after_v1_2, VLANID_NONE
import logging
import copy
import heapq
import networkx as nx
from firewall import Firewall


class ControllerApp(app_manager.OSKenApp):
    OFP_VERSIONS = [ofproto_v1_0.OFP_VERSION]

    SWITCH_FLOW_COOKIE = 0x305001
    SWITCH_FLOW_PRIORITY = 100
    ARP_PACKETIN_PRIORITY = 200
    # Bonus: poll dhcp.clean_expired_lease(); interval only lives in controller
    DHCP_LEASE_CLEANUP_INTERVAL = 5
    # Bonus: shortest-path algorithm for flow install (override via CS305_ROUTING)
    ROUTING_ALGORITHMS = ('dijkstra', 'bellman-ford', 'bfs', 'networkx')
    DEFAULT_ROUTING_ALGO = 'dijkstra'

    def __init__(self, *args, **kwargs):
        super(ControllerApp, self).__init__(*args, **kwargs)
        self.firewall = Firewall()
        self.switch_ofctl = {}
        self.adjacency = defaultdict(dict)
        self.host_table = {}
        self.ip_to_mac = {}
        self._routing_algo = self._resolve_routing_algo()
        self.logger.info(
            'Routing algorithm: %s (export CS305_ROUTING=%s to switch)',
            self._routing_algo,
            '|'.join(self.ROUTING_ALGORITHMS),
        )
        self.logger.info(
            'DHCP bonus: lease_time=%ss, cleanup every %ss (expect CLEAN ~%ss after ACK)',
            DHCPServer.lease_time,
            self.DHCP_LEASE_CLEANUP_INTERVAL,
            DHCPServer.lease_time + self.DHCP_LEASE_CLEANUP_INTERVAL,
        )
        hub.spawn(self._dhcp_lease_cleanup_loop)

    def _dhcp_lease_cleanup_loop(self):
        interval = max(1, self.DHCP_LEASE_CLEANUP_INTERVAL)
        while True:
            hub.sleep(interval)
            try:
                if DHCPServer.lease_record:
                    now = time.time()
                    soonest = min(DHCPServer.lease_record.values()) - now
                    self.logger.info(
                        'DHCP leases: %d active, soonest expires in %.0fs',
                        len(DHCPServer.lease_record),
                        max(0.0, soonest),
                    )
                n = DHCPServer.clean_expired_lease()
                if n:
                    self.logger.info('DHCP lease cleanup: recycled %d binding(s)', n)
            except Exception as e:
                self.logger.error('DHCP lease cleanup failed: %s', e)

    def _register_host(self, host):
        ipv4_addr = host.ipv4[0] if host.ipv4 else None
        self.host_table[host.mac] = {
            'dpid': host.port.dpid,
            'port': host.port.port_no,
            'ipv4': ipv4_addr,
        }
        if ipv4_addr:
            self.ip_to_mac[ipv4_addr] = host.mac

    def _build_adjacency(self):
        self.adjacency = defaultdict(dict)
        for link in topo_api.get_all_link(self):
            self.adjacency[link.src.dpid][link.dst.dpid] = link.src.port_no

    def _resolve_routing_algo(self):
        algo = os.environ.get('CS305_ROUTING', self.DEFAULT_ROUTING_ALGO).strip().lower()
        if algo not in self.ROUTING_ALGORITHMS:
            self.logger.warning(
                'Unknown CS305_ROUTING=%r, falling back to %s',
                algo,
                self.DEFAULT_ROUTING_ALGO,
            )
            return self.DEFAULT_ROUTING_ALGO
        return algo

    def _all_switch_dpids(self):
        return set(self.adjacency.keys()) | set(self.switch_ofctl.keys())

    def _dijkstra_with_prev(self, src_dpid):
        dist = {src_dpid: 0}
        prev = {}
        pq = [(0, src_dpid)]
        while pq:
            d, u = heapq.heappop(pq)
            if d > dist.get(u, float('inf')):
                continue
            for v in self.adjacency.get(u, {}):
                nd = d + 1
                if v not in dist or nd < dist[v]:
                    dist[v] = nd
                    prev[v] = u
                    heapq.heappush(pq, (nd, v))
        return dist, prev

    def _bellman_ford_with_prev(self, src_dpid):
        nodes = self._all_switch_dpids()
        dist = {n: float('inf') for n in nodes}
        prev = {}
        if src_dpid not in nodes:
            nodes.add(src_dpid)
            dist[src_dpid] = 0
        else:
            dist[src_dpid] = 0
        for _ in range(max(0, len(nodes) - 1)):
            updated = False
            for u in list(nodes):
                if dist[u] == float('inf'):
                    continue
                for v in self.adjacency.get(u, {}):
                    nd = dist[u] + 1
                    if nd < dist.get(v, float('inf')):
                        dist[v] = nd
                        prev[v] = u
                        updated = True
            if not updated:
                break
        reachable = {k: int(v) for k, v in dist.items() if v != float('inf')}
        return reachable, prev

    def _bfs_with_prev(self, src_dpid):
        dist = {src_dpid: 0}
        prev = {}
        queue = deque([src_dpid])
        while queue:
            u = queue.popleft()
            for v in self.adjacency.get(u, {}):
                if v not in dist:
                    dist[v] = dist[u] + 1
                    prev[v] = u
                    queue.append(v)
        return dist, prev

    def _networkx_with_prev(self, src_dpid):
        graph = nx.Graph()
        for u, neighbors in self.adjacency.items():
            for v in neighbors:
                graph.add_edge(u, v)
        if src_dpid not in graph:
            graph.add_node(src_dpid)
        if graph.number_of_nodes() == 0:
            return {src_dpid: 0}, {}
        lengths = nx.single_source_shortest_path_length(graph, src_dpid)
        paths = nx.single_source_shortest_path(graph, src_dpid)
        prev = {}
        for node, path in paths.items():
            if len(path) >= 2:
                prev[node] = path[-2]
        return dict(lengths), prev

    def _shortest_distances_and_prev(self, src_dpid):
        if self._routing_algo == 'dijkstra':
            return self._dijkstra_with_prev(src_dpid)
        if self._routing_algo == 'bellman-ford':
            return self._bellman_ford_with_prev(src_dpid)
        if self._routing_algo == 'bfs':
            return self._bfs_with_prev(src_dpid)
        if self._routing_algo == 'networkx':
            return self._networkx_with_prev(src_dpid)
        return self._dijkstra_with_prev(src_dpid)

    def _shortest_distances(self, src_dpid):
        dist, _ = self._shortest_distances_and_prev(src_dpid)
        return dist

    def _switch_path_from_prev(self, src_dpid, dst_dpid, prev):
        if src_dpid == dst_dpid:
            return [src_dpid]
        if dst_dpid not in prev:
            return []
        path = [dst_dpid]
        cur = prev[dst_dpid]
        while cur != src_dpid:
            path.append(cur)
            cur = prev.get(cur)
            if cur is None:
                return []
        path.append(src_dpid)
        path.reverse()
        return path

    def _get_out_port_from_dist(self, sw, dst_dpid, dst_port, dist):
        if sw == dst_dpid:
            return dst_port
        if sw not in dist:
            return None
        for neighbor, port in self.adjacency.get(sw, {}).items():
            if neighbor in dist and dist[neighbor] < dist[sw]:
                return port
        return None

    def _install_arp_packetin(self, dpid):
        ofctl = self.switch_ofctl.get(dpid)
        if not ofctl:
            return
        ofctl.set_packetin_flow(
            cookie=self.SWITCH_FLOW_COOKIE,
            priority=self.ARP_PACKETIN_PRIORITY,
            dl_type=ether_types.ETH_TYPE_ARP,
        )

    def _install_forwarding_rules(self, hosts):
        if not hosts:
            return

        all_switches = set(self.switch_ofctl.keys())
        for dst in hosts:
            dst_dpid = dst.port.dpid
            dst_port = dst.port.port_no
            dst_mac = dst.mac
            dist = self._shortest_distances(dst_dpid)

            for sw in all_switches:
                if sw not in dist:
                    continue
                out_port = self._get_out_port_from_dist(sw, dst_dpid, dst_port, dist)
                if out_port is None:
                    continue
                ofctl = self.switch_ofctl.get(sw)
                if not ofctl:
                    continue
                dp = ofctl.dp
                actions = [dp.ofproto_parser.OFPActionOutput(out_port)]
                ofctl.set_flow(
                    cookie=self.SWITCH_FLOW_COOKIE,
                    priority=self.SWITCH_FLOW_PRIORITY,
                    dl_type=ether_types.ETH_TYPE_IP,
                    dl_vlan=VLANID_NONE,
                    dl_dst=dst_mac,
                    actions=actions,
                )

        self._print_all_paths(hosts)

    def _build_host_name_map(self, hosts):
        sorted_hosts = sorted(
            hosts,
            key=lambda h: h.ipv4[0] if h.ipv4 else h.mac,
        )
        return {h.mac: 'h%d' % (i + 1) for i, h in enumerate(sorted_hosts)}

    def _host_label(self, host, name_map):
        ip = host.ipv4[0] if host.ipv4 else host.mac
        return '%s(%s)' % (name_map.get(host.mac, host.mac), ip)

    def _compute_path(self, src, dst, name_map, mac_to_host):
        _, prev = self._shortest_distances_and_prev(src.port.dpid)
        switch_path = self._switch_path_from_prev(
            src.port.dpid, dst.port.dpid, prev,
        )
        if not switch_path:
            return [], -1
        labels = [name_map.get(src.mac, src.mac)]
        labels.extend('s%d' % dpid for dpid in switch_path)
        labels.append(name_map.get(dst.mac, dst.mac))
        return labels, len(labels) - 1

    def _print_all_paths(self, hosts):
        if len(hosts) < 2:
            return

        name_map = self._build_host_name_map(hosts)
        mac_to_host = {h.mac: h for h in hosts}
        self.logger.info('Host pair paths (routing=%s):', self._routing_algo)

        for src in hosts:
            for dst in hosts:
                if src.mac == dst.mac:
                    continue
                labels, distance = self._compute_path(src, dst, name_map, mac_to_host)
                if distance < 0:
                    continue
                path_str = ' -> '.join(labels)
                self.logger.info(
                    'Path from %s to %s: %s, distance=%d',
                    self._host_label(src, name_map),
                    self._host_label(dst, name_map),
                    path_str,
                    distance,
                )

    def _compute_switch_path(self, src_dpid, dst_dpid):
        if src_dpid == dst_dpid:
            return ['s%d' % src_dpid], 0
        _, prev = self._shortest_distances_and_prev(src_dpid)
        switch_path = self._switch_path_from_prev(src_dpid, dst_dpid, prev)
        if not switch_path:
            return [], -1
        return ['s%d' % n for n in switch_path], len(switch_path) - 1

    def _print_topology_nx(self, hosts):
        graph = nx.Graph()
        switches = set(self.adjacency.keys()) | set(self.switch_ofctl.keys())

        for dpid in switches:
            graph.add_node('s%d' % dpid, node_type='switch')

        for src, neighbors in self.adjacency.items():
            for dst in neighbors:
                graph.add_edge('s%d' % src, 's%d' % dst)

        if hosts:
            name_map = self._build_host_name_map(hosts)
            for host in hosts:
                label = name_map.get(host.mac, host.mac)
                graph.add_node(label, node_type='host')
                graph.add_edge(label, 's%d' % host.port.dpid)

        self.logger.info('=' * 60)
        self.logger.info(
            'Topology (networkx): %d nodes, %d edges',
            graph.number_of_nodes(),
            graph.number_of_edges(),
        )
        self.logger.info('Nodes: %s', sorted(graph.nodes()))
        self.logger.info('Edges: %s', sorted(graph.edges()))

    def _print_switch_paths(self):
        switches = sorted(set(self.adjacency.keys()) | set(self.switch_ofctl.keys()))
        if len(switches) < 2:
            return

        self.logger.info(
            'Shortest paths between switches (routing=%s):',
            self._routing_algo,
        )
        for src in switches:
            for dst in switches:
                if src == dst:
                    continue
                labels, distance = self._compute_switch_path(src, dst)
                if distance < 0:
                    continue
                self.logger.info(
                    '%s to %s: %s, %d edges',
                    's%d' % src,
                    's%d' % dst,
                    ' -> '.join(labels),
                    distance,
                )

    def update_network(self):
        self._build_adjacency()
        hosts = topo_api.get_all_host(self)
        for host in hosts:
            self._register_host(host)
        self._print_topology_nx(hosts)
        self._print_switch_paths()
        self._install_forwarding_rules(hosts)

    def _handle_arp(self, datapath, in_port, pkt):
        pkt_eth = pkt.get_protocol(ethernet.ethernet)
        pkt_arp = pkt.get_protocol(arp.arp)
        if not pkt_eth or not pkt_arp:
            return

        if pkt_arp.opcode != arp.ARP_REQUEST:
            return

        dst_ip = pkt_arp.dst_ip
        if dst_ip not in self.ip_to_mac:
            for host in topo_api.get_all_host(self):
                self._register_host(host)

        if dst_ip not in self.ip_to_mac:
            self.logger.warning('ARP request for unknown IP %s', dst_ip)
            return

        target_mac = self.ip_to_mac[dst_ip]
        ofctl = OfCtl.factory(datapath, self.logger)
        ofctl.send_arp(
            arp.ARP_REPLY,
            VLANID_NONE,
            pkt_eth.src,
            target_mac,
            dst_ip,
            pkt_arp.src_ip,
            pkt_arp.src_mac,
            datapath.ofproto.OFPP_CONTROLLER,
            in_port,
        )

    @set_ev_cls(event.EventSwitchEnter)
    def handle_switch_add(self, ev):
        """
        Event handler indicating a switch has come online.
        """
        sw = ev.switch
        dpid = sw.dp.id
        ofctl = OfCtl.factory(sw.dp, self.logger)
        self.switch_ofctl[dpid] = ofctl
        self.firewall.install_rules(self.switch_ofctl)
        self._install_arp_packetin(dpid)
        self.update_network()

    @set_ev_cls(event.EventSwitchLeave)
    def handle_switch_delete(self, ev):
        """
        Event handler indicating a switch has been removed
        """
        dpid = ev.switch.dp.id
        if dpid in self.switch_ofctl:
            del self.switch_ofctl[dpid]
        self.update_network()

    @set_ev_cls(event.EventHostAdd)
    def handle_host_add(self, ev):
        """
        Event handler indiciating a host has joined the network
        This handler is automatically triggered when a host sends an ARP response.
        """
        self._register_host(ev.host)
        self.update_network()

    @set_ev_cls(event.EventLinkAdd)
    def handle_link_add(self, ev):
        """
        Event handler indicating a link between two switches has been added
        """
        self.update_network()

    @set_ev_cls(event.EventLinkDelete)
    def handle_link_delete(self, ev):
        """
        Event handler indicating when a link between two switches has been deleted
        """
        self.update_network()

    @set_ev_cls(event.EventPortModify)
    def handle_port_modify(self, ev):
        """
        Event handler for when any switch port changes state.
        This includes links for hosts as well as links between switches.
        """
        self.update_network()

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):
        try:
            msg = ev.msg
            datapath = msg.datapath
            pkt = packet.Packet(data=msg.data)
            pkt_dhcp = pkt.get_protocols(dhcp.dhcp)
            inPort = msg.in_port
            if pkt_dhcp:
                DHCPServer.handle_dhcp(datapath, inPort, pkt)
            elif pkt.get_protocol(arp.arp):
                self._handle_arp(datapath, inPort, pkt)
            return
        except Exception as e:
            self.logger.error(e)
