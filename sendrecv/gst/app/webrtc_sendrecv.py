#!/usr/bin/env python
# -*- coding: utf-8 -*-
""""""

import argparse
from datetime import datetime
import asyncio
from functools import wraps
import json
import os
import random
import ssl
import sys
import time

import gi

gi.require_version("Gst", "1.0")
gi.require_version("GstWebRTC", "1.0")
gi.require_version("GstSdp", "1.0")
from gi.repository import Gst
from gi.repository import GstWebRTC
from gi.repository import GstSdp
import websockets
from websockets.version import version as wsv
from websockets.uri import parse_uri

WEBRTC_BIN_PIPELINE = """
videoconvert
! queue
! vp8enc
  deadline=1
! rtpvp8pay
! queue 
! application/x-rtp,media=video,encoding-name=VP8,payload=97
! webrtcbin
  name=sendrecv
  bundle-policy=max-bundle
  stun-server=stun://stun.l.google.com:19302
"""

SRC_PIPELINE = """
videotestsrc
  is-live=true
  pattern=ball
! timeoverlay
  font-desc="Sans, 36"
  halignment=center
  valignment=center
! tee
  name=t
"""

PIPELINE_DESC = """
videotestsrc
  is-live=true
  pattern=ball
! timeoverlay
  font-desc="Sans, 36"
  halignment=center
  valignment=center
! tee
  name=t

t.
! videoconvert
! queue
! vp8enc
  deadline=1
! rtpvp8pay
! queue 
! application/x-rtp,media=video,encoding-name=VP8,payload=97
! webrtcbin
  name=sendrecv
  bundle-policy=max-bundle
  stun-server=stun://stun.l.google.com:19302
"""
# t.
# ! queue
# ! videoconvert
# ! xvimagesink
# '''


def traced(func):
    @wraps(func)
    def wrapper(*a, **kw):
        print(f"CALL: {func.__qualname__}(*{a},**{kw})")
        ret = func(*a, **kw)
        print(f"EXIT: {func.__qualname__} -> {ret}")
        return ret

    return wrapper


def traced_async(func):
    @wraps(func)
    async def wrapper(*a, **kw):
        print(f"CALL: {func.__qualname__}(*{a},**{kw})")
        ret = await func(*a, **kw)
        print(f"EXIT: {func.__qualname__} -> {ret}")
        return ret

    return wrapper


class GstPlayer:

    @traced
    def __init__(
        self,
        webrtcclient,
    ):
        self.pipe = None
        self.webrtcclient = webrtcclient

    def start_pipeline(self, source_pipeline):
        self.webrtc_bin = Gst.parse_bin_from_description(WEBRTC_BIN_PIPELINE)
        self.pipe = Gst.parse_launch(PIPELINE_DESC)  # TODO cambiar por Gst,parse_bin_from_descriptiuon y conectar a la pipa preexistente
        self.webrtc = self.pipe.get_by_name("sendrecv")
        self.webrtc.connect("on-negotiation-needed", self.on_negotiation_needed)
        self.webrtc.connect("on-ice-candidate", self.webrtcclient.send_ice_candidate_message)
        self.webrtc.connect("pad-added", self.on_incoming_stream)
        self.pipe.set_state(Gst.State.PLAYING)
        

    def on_negotiation_needed(self, element):
        promise = Gst.Promise.new_with_change_func(self.on_offer_created, element, None)
        element.emit("create-offer", None, promise)

    def on_offer_created(self, promise, _, __):
        promise.wait()
        reply = promise.get_reply()
        offer = reply.get_value("offer")
        promise = Gst.Promise.new()
        self.webrtc.emit("set-local-description", offer, promise)
        promise.interrupt()
        self.webrtcclient.send_sdp_offer(offer)

    def on_incoming_stream(self, _, pad):
        if pad.direction != Gst.PadDirection.SRC:
            return

        decodebin = Gst.ElementFactory.make("decodebin")
        decodebin.connect("pad-added", self.on_incoming_decodebin_stream)
        self.pipe.add(decodebin)
        decodebin.sync_state_with_parent()
        self.webrtc.link(decodebin)

    def on_incoming_decodebin_stream(self, _, pad):
        if not pad.has_current_caps():
            print(pad, "has no caps, ignoring")
            return

        caps = pad.get_current_caps()
        assert len(caps)
        s = caps[0]
        name = s.get_name()
        if name.startswith("video"):
            q = Gst.ElementFactory.make("queue")
            conv = Gst.ElementFactory.make("videoconvert")
            sink = Gst.ElementFactory.make("autovideosink")
            self.pipe.add(q, conv, sink)
            self.pipe.sync_children_states()
            pad.link(q.get_static_pad("sink"))
            q.link(conv)
            conv.link(sink)
        elif name.startswith("audio"):
            q = Gst.ElementFactory.make("queue")
            conv = Gst.ElementFactory.make("audioconvert")
            resample = Gst.ElementFactory.make("audioresample")
            sink = Gst.ElementFactory.make("autoaudiosink")
            self.pipe.add(q, conv, resample, sink)
            self.pipe.sync_children_states()
            pad.link(q.get_static_pad("sink"))
            q.link(conv)
            conv.link(resample)
            resample.link(sink)

    def close_pipeline(self):
        self.pipe.set_state(Gst.State.NULL)
        self.pipe = None
        self.webrtc = None

class WebRTCClient:
    @traced
    def __init__(
        self,
        id_,
        peer_id,
        server,
    ):
        self.id_ = id_
        self.conn = None
        # self.webrtc = None
        self.peer_id = peer_id
        if not server:
            raise ValueError
        self.server = server or "wss://webrtc.nirbheek.in:8443"

        self.player = GstPlayer(self)


        # self.server = 'wss://webrtc.nirbheek.in:8443'

    @traced_async
    async def connect(self):
        wsuri = traced(parse_uri)(self.server)
        if wsuri.secure:
            sslctx = ssl.create_default_context(purpose=ssl.Purpose.CLIENT_AUTH)
        else:
            sslctx = None
        self.conn = await websockets.connect(self.server, ssl=sslctx)
        await self.conn.send("HELLO %d" % self.id_)

    async def setup_call(self):
        await self.conn.send("SESSION {}".format(self.peer_id))

    def send_sdp_offer(self, offer):
        text = offer.sdp.as_text()
        print("Sending offer:\n%s" % text)
        msg = json.dumps({"sdp": {"type": "offer", "sdp": text}})
        loop = asyncio.new_event_loop()
        loop.run_until_complete(self.conn.send(msg))
        loop.close()


    def send_ice_candidate_message(self, _, mlineindex, candidate):
        icemsg = json.dumps(
            {"ice": {"candidate": candidate, "sdpMLineIndex": mlineindex}}
        )
        loop = asyncio.new_event_loop()
        loop.run_until_complete(self.conn.send(icemsg))
        loop.close()


    def start_pipeline(self):
        self.player.start_pipeline()

    @property
    def webrtc(self):
        return self.player.webrtc


    @property
    def pipe(self):
        return self.player.pipe

    def handle_sdp(self, message):
        assert self.webrtc
        msg = json.loads(message)
        if "sdp" in msg:
            sdp = msg["sdp"]
            assert sdp["type"] == "answer"
            sdp = sdp["sdp"]
            print("Received answer:\n%s" % sdp)
            res, sdpmsg = GstSdp.SDPMessage.new()
            GstSdp.sdp_message_parse_buffer(bytes(sdp.encode()), sdpmsg)
            answer = GstWebRTC.WebRTCSessionDescription.new(
                GstWebRTC.WebRTCSDPType.ANSWER, sdpmsg
            )
            promise = Gst.Promise.new()
            self.webrtc.emit("set-remote-description", answer, promise)
            promise.interrupt()
        elif "ice" in msg:
            ice = msg["ice"]
            candidate = ice["candidate"]
            sdpmlineindex = ice["sdpMLineIndex"]
            self.webrtc.emit("add-ice-candidate", sdpmlineindex, candidate)

    def close_pipeline(self):
        self.player.close_pipeline()

    async def loop(self):
        assert self.conn
        async for message in self.conn:
            if message == "HELLO":
                await self.setup_call()
            elif message == "SESSION_OK":
                self.start_pipeline()
            elif message.startswith("ERROR"):
                print(message)
                self.close_pipeline()
                return 1
            else:
                self.handle_sdp(message)
        self.close_pipeline()
        return 0

    async def stop(self):
        if self.conn:
            await self.conn.close()
        self.conn = None


def check_plugins():
    needed = [
        "opus",
        "vpx",
        "nice",
        "webrtc",
        "dtls",
        "srtp",
        "rtp",
        "rtpmanager",
        "videotestsrc",
        "audiotestsrc",
    ]
    missing = list(filter(lambda p: Gst.Registry.get().find_plugin(p) is None, needed))
    if len(missing):
        print("Missing gstreamer plugins:", missing)
        return False
    return True


def main(args):

    our_id = random.randrange(10, 10000)
    c = WebRTCClient(our_id, args.peerid, args.server)

    loop = asyncio.get_event_loop()
    loop.run_until_complete(c.connect())
    res = loop.run_until_complete(c.loop())

    sys.exit(res)


def main_retry():
    Gst.init(None)
    if not check_plugins():
        sys.exit(1)
    parser = argparse.ArgumentParser()
    parser.add_argument("peerid", help="String ID of the peer to connect to")
    parser.add_argument(
        "--server", help='Signalling server to connect to, eg "wss://127.0.0.1:8443"'
    )
    args = parser.parse_args()
    print("Waiting a few seconds for you to open the browser at localhost:8080")
    time.sleep(10)
    main(args)


if __name__ == "__main__":
    main_retry()