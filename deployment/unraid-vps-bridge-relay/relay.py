#!/usr/bin/env python3
"""Allow only Home-AI-Tools to reach the fixed private VPS bridge endpoint."""
import argparse
import select
import socket
import socketserver

UPSTREAM = ("100.118.61.115", 9301)
ALLOWED_CLIENT = "172.23.0.9"  # Home-AI-Tools on the private voiceai Docker network
LISTEN = "172.23.0.1"          # voiceai network gateway only


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        if self.client_address[0] != self.server.allowed_client:
            return
        client = self.request
        try:
            upstream = socket.create_connection(UPSTREAM, timeout=5)
        except OSError:
            return
        with upstream:
            client.settimeout(None)
            upstream.settimeout(None)
            sockets = [client, upstream]
            while True:
                readable, _, _ = select.select(sockets, [], [], 70)
                if not readable:
                    return
                for source in readable:
                    try:
                        data = source.recv(65536)
                    except OSError:
                        return
                    if not data:
                        return
                    destination = upstream if source is client else client
                    try:
                        destination.sendall(data)
                    except OSError:
                        return


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    request_queue_size = 16


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen", default=LISTEN)
    parser.add_argument("--allowed-client", default=ALLOWED_CLIENT)
    args = parser.parse_args()
    Handler.allowed_client = args.allowed_client
    with Server((args.listen, 9301), Handler) as server:
        server.allowed_client = args.allowed_client
        server.serve_forever(poll_interval=1)


if __name__ == "__main__":
    main()
