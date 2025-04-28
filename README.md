# Threaded TCP Chat

A multi-client chat application using Python, TCP sockets, and threading.

## Protocol

*   Messages are Python lists -> JSON -> UTF-8 bytes.
*   Each message is prefixed by its byte length (4-byte unsigned int, big-endian).
*   Types: `["START", name]`, `["BROADCAST", sender, text]`, `["PRIVATE", sender, text, recipient]`, `["EXIT", name]`, `["START_FAIL", "Server", reason]`.

## How to Run

1.  **Server:**
    ```bash
    python server.py
    ```
    (Listens on ports below. Press Ctrl+C to stop.)

2.  **Client:** (Open a new terminal for each)
    ```bash
    python client.py [SERVER_IP]
    ```
    (Defaults to `127.0.0.1` if `SERVER_IP` is omitted. Enter a screen name when prompted.)

## Ports Used

*   Server Reading Port: `65432` (Clients SEND here)
*   Server Writing Port: `65433` (Clients RECEIVE here)

## Client Commands

*   **Broadcast:** Type message and press Enter.
    `Alice> Hello all!`
*   **Private:** Type `@recipient message` and press Enter.
    `Alice> @Bob Secret message`
*   **Exit:** Type `!exit` and press Enter (or use Ctrl+C / Ctrl+D).
    `Alice> !exit`
