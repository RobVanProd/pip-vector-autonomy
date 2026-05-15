import argparse
import asyncio
import ssl


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


async def _handle_client(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    backend_host: str,
    backend_port: int,
    backend_sni: str,
) -> None:
    peer = client_writer.get_extra_info("peername")
    backend_ssl = ssl._create_unverified_context()
    backend_ssl.set_alpn_protocols(["h2", "http/1.1"])
    try:
        backend_reader, backend_writer = await asyncio.open_connection(
            backend_host,
            backend_port,
            ssl=backend_ssl,
            server_hostname=backend_sni,
        )
    except Exception as exc:
        print(f"backend connect failed for {peer}: {exc}", flush=True)
        client_writer.close()
        await client_writer.wait_closed()
        return

    print(f"relay connected {peer} -> {backend_host}:{backend_port}", flush=True)
    await asyncio.gather(
        _pipe(client_reader, backend_writer),
        _pipe(backend_reader, client_writer),
        return_exceptions=True,
    )


async def main() -> None:
    parser = argparse.ArgumentParser(description="TLS re-encryption relay for WirePod.")
    parser.add_argument("--listen-host", default="0.0.0.0")
    parser.add_argument("--listen-port", type=int, default=443)
    parser.add_argument("--backend-host", default="127.0.0.1")
    parser.add_argument("--backend-port", type=int, default=8443)
    parser.add_argument("--backend-sni", default="escapepod.local")
    parser.add_argument("--cert", required=True)
    parser.add_argument("--key", required=True)
    args = parser.parse_args()

    server_ssl = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ssl.load_cert_chain(args.cert, args.key)
    server_ssl.set_alpn_protocols(["h2", "http/1.1"])

    server = await asyncio.start_server(
        lambda r, w: _handle_client(r, w, args.backend_host, args.backend_port, args.backend_sni),
        args.listen_host,
        args.listen_port,
        ssl=server_ssl,
    )
    addrs = ", ".join(str(sock.getsockname()) for sock in server.sockets or [])
    print(f"tls relay listening on {addrs}, backend {args.backend_host}:{args.backend_port}", flush=True)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
