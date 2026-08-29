"""Bread-parity ChatAttempt: what optional features (tools/think/images/format/logprobs) a chat attempt may use."""
from dataclasses import dataclass

@dataclass
class ChatAttempt:
    use_tools: bool = True
    use_think: bool = True
    use_images: bool = True
    use_format: bool = True
    use_logprobs: bool = True

    def without(self, feature: str) -> "ChatAttempt":
        if feature == "tools":
            return ChatAttempt(
                use_tools=False,
                use_think=self.use_think,
                use_images=self.use_images,
                use_format=self.use_format,
                use_logprobs=self.use_logprobs,
            )
        if feature == "think":
            return ChatAttempt(
                use_tools=self.use_tools,
                use_think=False,
                use_images=self.use_images,
                use_format=self.use_format,
                use_logprobs=self.use_logprobs,
            )
        if feature == "images":
            return ChatAttempt(
                use_tools=self.use_tools,
                use_think=self.use_think,
                use_images=False,
                use_format=self.use_format,
                use_logprobs=self.use_logprobs,
            )
        if feature == "format":
            return ChatAttempt(
                use_tools=self.use_tools,
                use_think=self.use_think,
                use_images=self.use_images,
                use_format=False,
                use_logprobs=self.use_logprobs,
            )
        if feature == "logprobs":
            return ChatAttempt(
                use_tools=self.use_tools,
                use_think=self.use_think,
                use_images=self.use_images,
                use_format=self.use_format,
                use_logprobs=False,
            )
        return self

    def disabled_features(self) -> list[str]:
        disabled: list[str] = []
        if not self.use_tools:
            disabled.append("tools")
        if not self.use_think:
            disabled.append("think")
        if not self.use_images:
            disabled.append("images")
        if not self.use_format:
            disabled.append("format")
        if not self.use_logprobs:
            disabled.append("logprobs")
        return disabled
