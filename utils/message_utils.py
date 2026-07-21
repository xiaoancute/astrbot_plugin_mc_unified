class MessageUtils:
    """消息工具类"""

    @staticmethod
    def format_mc_message(player: str, message: str, prefix: str = "[MC]") -> str:
        return f"{prefix} {player}: {message}"

    @staticmethod
    def format_qq_message(sender_name: str, message: str, prefix: str = "[QQ]") -> str:
        return f"{prefix} {sender_name}: {message}"

    @staticmethod
    def split_long_message(message: str, max_length: int = 200) -> list:
        lines = message.split("\n")
        chunks = []
        current = ""

        for line in lines:
            if len(line) > max_length:
                if current:
                    chunks.append(current)
                    current = ""

                for i in range(0, len(line), max_length):
                    chunks.append(line[i : i + max_length])
            else:
                test_chunk = current + ("\n" if current else "") + line
                if len(test_chunk) > max_length:
                    if current:
                        chunks.append(current)
                    current = line
                else:
                    current = test_chunk

        if current:
            chunks.append(current)

        return chunks
