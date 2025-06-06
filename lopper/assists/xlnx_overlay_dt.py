#/*
# * Copyright (c) 2021 Xilinx Inc. All rights reserved.
# * Copyright (c) 2023, Advanced Micro Devices, Inc. All rights reserved.
# *
# * Author:
# *       Appana Durga Kedareswara rao <appana.durga.rao@xilinx.com>
# *       Naga Sureshkumar Relli <naga.sureshkumar.relli@xilinx.com>
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

import struct
import sys
import types
import os
import getopt
import re
import subprocess
from pathlib import Path
from pathlib import PurePath
from lopper import Lopper
from lopper import LopperFmt
import lopper
from re import *
import yaml
import glob
from collections import OrderedDict

sys.path.append(os.path.dirname(__file__))
from baremetalconfig_xlnx import *

def is_compat( node, compat_string_to_test ):
    if re.search( "module,xlnx_overlay_dt", compat_string_to_test):
        return xlnx_generate_overlay_dt
    return ""

def get_label(sdt, symbol_node, node):
    prop_dict = Lopper.node_properties_as_dict(sdt.FDT, symbol_node.abs_path, False)
    match = [label for label,node_abs in prop_dict.items() if re.match(node_abs[0], node.abs_path) and len(node_abs[0]) == len(node.abs_path)]
    if match:
        return match[0]
    else:
        return None

def remove_node_ref(sdt, tgt_node, ref_node):
    prop_dict = ref_node.__props__.copy()
    match_label_list = []
    for node in sdt.tree[tgt_node].subnodes():
        matched_label = get_label(sdt, ref_node, node)
        if matched_label:
            match_label_list.append(matched_label)
    for prop,node1 in prop_dict.items():
        if prop not in match_label_list:
            sdt.tree['/' + ref_node.name].delete(prop)

def usage():
    prog = os.path.basename(sys.argv[0])
    print('Usage: %s <system device tree> -- <xlnx_overlay_dt.py> <machine name> <configuration>' % prog)
    print('  machine name:         cortexa53-zynqmp or cortexa72-versal')
    print('  configuration:        full or dfx or external-fpga-config' )

"""
This API generates the overlay dts file by taking pl.dtsi
generated from DTG++.
Args:
    tgt_node: is the baremetal config top level domain node number
    sdt:      is the system device-tree
    options:  There are two valid options
              Machine name as cortexa53-zynqmp or cortexa72-versal
              An optional argument full/dfx/external-fpga-config. The default will be full.
"""
def xlnx_generate_overlay_dt(tgt_node, sdt, options):
    root_node = sdt.tree[tgt_node]
    root_sub_nodes = root_node.subnodes()

    symbol_node = ""
    gic_node = ""
    platform = "None"
    config = "None"
    imux_node = ""
    try:
        platform = options['args'][0]
        config = options['args'][1]
    except:
        pass
    outfile = os.path.join(sdt.outdir, 'pl.dtsi')
    for node in root_sub_nodes:
        try:
            if node.name == "__symbols__":
                symbol_node = node
            if node.name == "interrupt-multiplex":
                imux_node = node
        except:
           pass

    # Get the list of nodes that are added to fragment@1
    # i,e fragment@1/overlay@1 node. we can ignore these nodes
    # while reading the tree to add nodes under fragment@2/overlay@2
    ignore_list = []
    phandle_dict = {"axistream-connected" : ['phandle 0'], "pcs-handle": ['phandle 0']}
    for node in root_sub_nodes:
        try:
            label_name = get_label(sdt, symbol_node, node)
            if (platform == "cortexa53-zynqmp" or platform == "psu_cortexa53_0") and label_name == "gic_a53":
                gic_node = node
            elif (platform == "cortexa72-versal" or platform == "psv_cortexa72_0") and label_name == "gic_a72":
                gic_node = node
            elif (platform == "psx_cortexa78_0"  or platform == "cortexa78_0") and label_name == "gic_a78":
                gic_node = node
            if re.search("afi0" , node.name) or re.search("afi1" , node.name) or re.search("afi2" , node.name) or re.search("afi3" , node.name) or re.search("clocking" , node.name):
               ignore_list.append(node)
        except:
           pass

    fpga_ignore_list = []
    for node in root_sub_nodes:

        try:
            if re.search("fpga-PR*" , node.name):
               fpga_ignore_list.append(node)

        except:
           pass

    # Initialize the variables to its defaults
    root = 1
    tab_len = 0
    parent_node = ""
    parent_tab = 0

    # If no config option is provided then the default is
    # full bit stream support
    if config == "None":
        config = "full"

    if config != "full" and config != "dfx" and config != "external-fpga-config":
        print('%s is not a valid argument' % str(config))
        usage()
        sys.exit(1)        

    pl_node = None
    has_valid_pl = False
    has_pl = False

    for node in root_sub_nodes:
        if node.name == "amba_pl":
            pl_node = node
            get_sub_node = node.subnodes()
            for node in get_sub_node:
                if node.propval('reg') != ['']:
                    has_valid_pl = True
                    break
        if has_valid_pl:
            break

    if pl_node:
        plat = DtbtoCStruct(outfile)

    for node in root_sub_nodes:
        set_ignore = 0

        try:
            path = node.abs_path
            ret = path.split('/')
            pl = 0
            rt = len(ret) - 1
            child_len = len(node.child_nodes)

            # Only check for the nodes under amba_pl
            for x in ret:
                if x == "amba_pl":
                   pl = 1

            if pl == 1:
                if child_len != 0 and node.name != "amba_pl":
                    if child_len > 1:
                        tab_len = 0
                    else:
                        tab_len = tab_len + int(child_len)

                if root == 1:
                    # Create overlay0: __overlay__ node as first node under fragment@0
                    plat.buf('/dts-v1/;')
                    plat.buf('\n/plugin/;')
                    if platform == "cortexa53-zynqmp" or platform == "cortexa9-zynq":
                        plat.buf('\n&fpga_full{')
                    else:
                        plat.buf('\n&fpga{')
                    try:
                        # There is no cortexa9-zynq platform but this is a place
                        # holder. If platform is microblaze then exit as dt overlays
                        # are not supported for microblaze platform.
                        #
                        # configuration "full" is used for Zynq 7000(full),
                        # ZynqMP(full) and Versal(segmented configuration) requires
                        # firmware-name dt property.
                        #
                        # configuration "dfx" is used for ZynqMP(DFx) and
                        # Versal(DFx). For Versal DFx Static we need
                        # external-fpga-config dt property.
                        if config == "full" and platform != "microblaze" or config == "dfx" and platform == "cortexa53-zynqmp":
                            plat.buf('\n\t%s' % node['firmware-name'])
                        elif config == "dfx" and platform != "microblaze" and platform != "cortexa9-zynq" and platform != "cortexa53-zynqmp":
                            plat.buf('\n\texternal-fpga-config;')
                        elif config == "external-fpga-config":
                            plat.buf('\n\texternal-fpga-config;')
                        else:
                            print('%s is not a valid Machine' % str(platform))
                            sys.exit(1)
                               
                    except:
                        pass

                    for inode in fpga_ignore_list:
                        label_name = get_label(sdt, symbol_node, inode)
                        plat.buf('\n\t%s: %s {' % (label_name, inode.name))
                        for p in inode.__props__.values():
                            if re.search("phandle =", str(p)) or str(p) == '':
                                continue
                            plat.buf('\n\t\t%s' % p)
                        plat.buf('\n\t};')
                    plat.buf('\n};')

                    # Add afi and clocking nodes to fragment@1     
                    if platform == "cortexa53-zynqmp" and config != "external-fpga-config" or platform == "cortexa9-zynq" and config != "external-fpga-config":
                        plat.buf('\n&amba{')
                        for inode in ignore_list:
                            label_name = get_label(sdt, symbol_node, inode)
                            plat.buf('\n\t%s: %s {' % (label_name, inode.name))
                            for p in inode.__props__.values():
                                if re.search("phandle =", str(p)) or str(p) == '':
                                    continue
                                plat.buf('\n\t\t%s' % p)
                            plat.buf('\n\t};')
                        plat.buf('\n};')
                    
                    if has_valid_pl:
                        has_valid_pl = False
                        has_pl = True
                        plat.buf('\n&amba{')
                    root = 0

                # Now add all the nodes except the nodes that are added
                # that are added under fragment@1
                for ignoreip in ignore_list:
                    if re.match(ignoreip.name , node.name):
                       set_ignore = 1

                for ignoreip in fpga_ignore_list:
                    if re.match(ignoreip.name , node.name):
                       set_ignore = 1

                if set_ignore == 1:
                    continue

                # Add all the nodes exists under amba_pl to fragment@2
                if node.name != "amba_pl":
                    if parent_tab == 1 and parent_node == node.parent:
                        plat.buf('\n')
                        plat.buf('\t' * int(rt))
                        plat.buf('};')
                        parent_tab = 0

                    label_name = get_label(sdt, symbol_node, node)
                    if label_name:
                        plat.buf('\n')
                        plat.buf('\t' * int(rt-1))
                        plat.buf('%s: %s {' % (label_name, node.name))
                        for p in node.__props__.values():
                            if re.search("phandle =", str(p)) or str(p) == '':
                                continue
                            plat.buf('\n')
                            plat.buf('\t' * int(rt))
                            phandle_prop = None
                            if p.name in phandle_dict:
                                # Get the phandle field in the value
                                phandle_val = phandle_dict[p.name][0].split()
                                phandle_index = phandle_val.index('phandle')
                                phandle_to_search = p.value[phandle_index]
                                node_found = [node for node in sdt.tree['/'].subnodes() if node.phandle == phandle_to_search]
                                if node_found:
                                    p.value[phandle_index] = f'&{node_found[0].label}'
                                    mod_val =  ' '.join(str(value) for value in p.value)
                                    node[p].value = f'<{mod_val}>'
                                    phandle_prop = str(p).replace('"', '')
                            if phandle_prop:
                                plat.buf('%s' % phandle_prop)
                            elif re.match("clocks =", str(p)):
                                plat.buf('%s' % node['clocks'])
                            else:
                                if p.name == "interrupt-parent":
                                    if gic_node and imux_node:
                                        if p.value[0] == imux_node.phandle:
                                            p = str(p).replace('imux', 'gic')
                                plat.buf('%s' % p)

                        plat.buf('\n')
                        plat.buf('\t' * int(rt-1))
                        if child_len < 1:
                            plat.buf('};')
                            for count in range(tab_len):
                                plat.buf('\n')
                                plat.buf('\t' * int(int(rt-2)-count))
                                plat.buf('};')

                            tab_len = 0
                        else:
                            if child_len > tab_len:
                                parent_node = node.parent
                                parent_tab = 1
        except:
           pass

    if parent_tab == 1:
        plat.buf('\n')
        plat.buf('\t' * int(rt))
        plat.buf('};')
    if has_pl:
        plat.buf('\n};')
    if pl_node:
        plat.out(''.join(plat.get_buf()))
        sdt.tree.delete(pl_node)
        remove_node_ref(sdt, tgt_node, sdt.tree['/__symbols__'])
        remove_node_ref(sdt, tgt_node, sdt.tree['/aliases'])

    return True
