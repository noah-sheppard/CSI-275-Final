# Final Project: Threaded TCP Chat
## Overview

This project implements a multi-client chat application using TCP sockets and threading in Python. Clients connect to a central server, choose a unique screen name, broadcast messages to all connected users, send private messages, and disconnect.

## Protocol Details

Communication follows a specific protocol:

1.  **Framing:** Every message is prefixed by its total byte length, encoded as a 4-byte unsigned integer (big-endian).
2.  **Content:** The message body is a Python list, serialized into a JSON string, and then encoded using UTF-8.
3.  **Message Types (List Structure):**
    *   `["START", <screen_name>]`: Client registers its name with the server.
    *   `["BROADCAST", <sender_name>, <message_text>]`: Send a message to all clients.
    *   `["PRIVATE", <sender_name>, <message_text>, <recipient_name>]`: Send a message to a specific client.
    *   `["EXIT", <screen_name>]`: Client signals disconnection.
    *   `["START_FAIL", "Server", <reason>]`: Server informs client why connection failed (e.g., name taken).

## How to Run

### Prerequisites

*   Python 3.x installed.

### Running the Server

1.  Open a terminal or command prompt.
2.  Navigate to the directory containing `server.py`.
3.  Run the server:
    ```bash
    python server.py
    ```
4.  The server will log that it's running and listening on the configured ports.
5.  Press `Ctrl+C` in the server terminal to stop it.

### Running the Client

1.  Open a **new, separate** terminal for *each* client instance.
2.  Navigate to the directory containing `client.py`.
3.  Run the client, optionally providing the server's IP address (defaults to localhost):
    ```bash
    python client.py [SERVER_IP_ADDRESS]
    ```
4.  Enter a unique screen name when prompted (no spaces or '@' symbols).
5.  If connection is successful, you can start chatting.

## Ports Used

*   **Server Reading Port:** `65432` (Clients SEND messages TO this port)
*   **Server Writing Port:** `65433` (Clients RECEIVE messages FROM this port)

Ensure these ports are accessible between the client and server machines.

## Client Usage

Once connected, use the following formats at the `YourName>` prompt:

*   **Broadcast:** Type your message and press Enter.
    ```
    Noah> Hello everyone!
    ```
*   **Private Message:** Type `@` followed by the recipient's screen name, a space, and your message. Press Enter.
    ```
    Noah> @Ryan How is the project going?
    ```
*   **Exit:** Type `!exit` and press Enter (or use `Ctrl+C`).
    ```
    Noah> !exit
    ```