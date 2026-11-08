from app.services.native_model import QuestionTokenizer


class CifarVisionLanguageModel:
    """A CIFAR-sized residual CNN fused with a trainable question encoder."""

    def __new__(cls, vocab_size: int, num_classes: int = 10):
        import torch
        import torch.nn as nn

        class ResidualBlock(nn.Module):
            def __init__(self, channels: int):
                super().__init__()
                self.layers = nn.Sequential(
                    nn.Conv2d(channels, channels, 3, padding=1, bias=False),
                    nn.BatchNorm2d(channels),
                    nn.ReLU(inplace=True),
                    nn.Conv2d(channels, channels, 3, padding=1, bias=False),
                    nn.BatchNorm2d(channels),
                )

            def forward(self, x):
                return torch.relu(x + self.layers(x))

        class DownsampleBlock(nn.Module):
            def __init__(self, input_channels: int, output_channels: int):
                super().__init__()
                self.main = nn.Sequential(
                    nn.Conv2d(input_channels, output_channels, 3, stride=2, padding=1, bias=False),
                    nn.BatchNorm2d(output_channels),
                    nn.ReLU(inplace=True),
                    nn.Conv2d(output_channels, output_channels, 3, padding=1, bias=False),
                    nn.BatchNorm2d(output_channels),
                )
                self.skip = nn.Sequential(
                    nn.Conv2d(input_channels, output_channels, 1, stride=2, bias=False),
                    nn.BatchNorm2d(output_channels),
                )

            def forward(self, x):
                return torch.relu(self.main(x) + self.skip(x))

        class _Model(nn.Module):
            def __init__(self, vocab_size: int, num_classes: int):
                super().__init__()
                self.vision = nn.Sequential(
                    nn.Conv2d(3, 64, 3, padding=1, bias=False),
                    nn.BatchNorm2d(64),
                    nn.ReLU(inplace=True),
                    ResidualBlock(64),
                    ResidualBlock(64),
                    DownsampleBlock(64, 128),
                    ResidualBlock(128),
                    DownsampleBlock(128, 256),
                    ResidualBlock(256),
                    nn.AdaptiveAvgPool2d(1),
                )
                self.question_embedding = nn.Embedding(vocab_size, 64, padding_idx=QuestionTokenizer.PAD)
                self.question_encoder = nn.GRU(64, 128, batch_first=True)
                self.fusion = nn.Sequential(
                    nn.Linear(256 + 128, 256),
                    nn.LayerNorm(256),
                    nn.ReLU(inplace=True),
                    nn.Dropout(0.25),
                )
                self.object_head = nn.Linear(256, num_classes)

            def forward(self, images, questions):
                visual = self.vision(images).flatten(1)
                encoded, _ = self.question_encoder(self.question_embedding(questions))
                mask = questions.ne(QuestionTokenizer.PAD).unsqueeze(-1)
                question_features = (encoded * mask).sum(dim=1) / mask.sum(dim=1).clamp_min(1)
                fused = self.fusion(torch.cat([visual, question_features], dim=1))
                return {"object": self.object_head(fused)}

        return _Model(vocab_size, num_classes)

