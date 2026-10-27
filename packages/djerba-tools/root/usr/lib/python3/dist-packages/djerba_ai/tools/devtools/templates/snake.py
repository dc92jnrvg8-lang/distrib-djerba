#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Snake — jeu généré par le Studio Djerba (PyQt6, aucune dépendance externe).

Flèches ou ZQSD pour diriger, Espace pour pause, Entrée pour rejouer.
Le meilleur score est conservé dans ~/.local/share/djerba/snake-score.
"""

import random
import sys
from pathlib import Path

from PyQt6.QtCore import QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import QApplication, QWidget

CELL = 26
COLUMNS, ROWS = 26, 20
SCORE_FILE = Path.home() / ".local" / "share" / "djerba" / "snake-score"
DIRECTIONS = {
    Qt.Key.Key_Up: (0, -1), Qt.Key.Key_Z: (0, -1), Qt.Key.Key_W: (0, -1),
    Qt.Key.Key_Down: (0, 1), Qt.Key.Key_S: (0, 1),
    Qt.Key.Key_Left: (-1, 0), Qt.Key.Key_Q: (-1, 0), Qt.Key.Key_A: (-1, 0),
    Qt.Key.Key_Right: (1, 0), Qt.Key.Key_D: (1, 0),
}


class Snake(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Snake — Djerba Studio")
        self.setFixedSize(COLUMNS * CELL, ROWS * CELL + 48)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.step)
        try:
            self.best = int(SCORE_FILE.read_text())
        except (OSError, ValueError):
            self.best = 0
        self.reset()

    def reset(self) -> None:
        middle = (COLUMNS // 2, ROWS // 2)
        self.body = [middle, (middle[0] - 1, middle[1]), (middle[0] - 2, middle[1])]
        self.direction = (1, 0)
        self.pending = (1, 0)
        self.score = 0
        self.over = False
        self.paused = False
        self.place_food()
        self.timer.start(120)

    def place_food(self) -> None:
        free = [(x, y) for x in range(COLUMNS) for y in range(ROWS) if (x, y) not in self.body]
        self.food = random.choice(free)

    def step(self) -> None:
        if self.paused or self.over:
            return
        self.direction = self.pending
        head = (self.body[0][0] + self.direction[0], self.body[0][1] + self.direction[1])
        if not (0 <= head[0] < COLUMNS and 0 <= head[1] < ROWS) or head in self.body[:-1]:
            self.over = True
            if self.score > self.best:
                self.best = self.score
                SCORE_FILE.parent.mkdir(parents=True, exist_ok=True)
                SCORE_FILE.write_text(str(self.best))
            self.update()
            return
        self.body.insert(0, head)
        if head == self.food:
            self.score += 10
            self.place_food()
            self.timer.setInterval(max(55, 120 - self.score // 5))
        else:
            self.body.pop()
        self.update()

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key in DIRECTIONS:
            dx, dy = DIRECTIONS[key]
            if (dx, dy) != (-self.direction[0], -self.direction[1]):
                self.pending = (dx, dy)
        elif key == Qt.Key.Key_Space:
            self.paused = not self.paused
            self.update()
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.over:
            self.reset()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#070B18"))
        painter.setPen(QColor("#F8FAFC"))
        painter.setFont(QFont("Inter", 14, QFont.Weight.DemiBold))
        painter.drawText(16, 31, f"Score : {self.score}")
        painter.drawText(self.width() - 170, 31, f"Record : {self.best}")
        painter.translate(0, 48)
        painter.setPen(QPen(QColor(255, 255, 255, 14)))
        for x in range(COLUMNS):
            for y in range(ROWS):
                painter.drawRect(x * CELL, y * CELL, CELL, CELL)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#FF7A59"))
        painter.drawEllipse(QRectF(self.food[0] * CELL + 5, self.food[1] * CELL + 5, CELL - 10, CELL - 10))
        for index, (x, y) in enumerate(self.body):
            painter.setBrush(QColor("#5EEAD4") if index == 0 else QColor("#00C2CB"))
            painter.drawRoundedRect(QRectF(x * CELL + 2, y * CELL + 2, CELL - 4, CELL - 4), 7, 7)
        if self.over or self.paused:
            painter.fillRect(0, 0, COLUMNS * CELL, ROWS * CELL, QColor(7, 11, 24, 190))
            painter.setPen(QColor("#F8FAFC"))
            painter.setFont(QFont("Inter", 24, QFont.Weight.Bold))
            text = "Partie terminée — Entrée pour rejouer" if self.over else "Pause"
            painter.drawText(QRectF(0, 0, COLUMNS * CELL, ROWS * CELL), Qt.AlignmentFlag.AlignCenter, text)


if __name__ == "__main__":
    if "--help" in sys.argv:
        print(__doc__)
        sys.exit(0)
    app = QApplication(sys.argv)
    window = Snake()
    window.show()
    sys.exit(app.exec())
