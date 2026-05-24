import hashlib
import numpy as np
from config import GRID_CELL_SIZE, SCENE_CHANGE_THRESHOLD


class RegionTracker:
    def __init__(self, screen_width, screen_height):
        self.cell_size = GRID_CELL_SIZE
        self.cols = max(1, screen_width // self.cell_size)
        self.rows = max(1, screen_height // self.cell_size)
        self.prev_hashes = {}

    def diff(self, image: np.ndarray):
        """Return list of (x, y, w, h, cell_image) for changed grid cells."""
        changed = []
        changed_count = 0
        total_cells = self.cols * self.rows
        h, w = image.shape[:2]

        for row in range(self.rows):
            for col in range(self.cols):
                x1 = col * self.cell_size
                y1 = row * self.cell_size
                x2 = min(x1 + self.cell_size, w)
                y2 = min(y1 + self.cell_size, h)

                cell = image[y1:y2, x1:x2]
                cell_hash = self._quick_hash(cell)

                prev = self.prev_hashes.get((col, row))
                if prev != cell_hash:
                    changed_count += 1
                    self.prev_hashes[(col, row)] = cell_hash
                    changed.append((x1, y1, x2 - x1, y2 - y1, cell.copy()))

        if total_cells > 0 and changed_count / total_cells > SCENE_CHANGE_THRESHOLD:
            self.prev_hashes.clear()
            changed = []
            for row in range(self.rows):
                for col in range(self.cols):
                    x1 = col * self.cell_size
                    y1 = row * self.cell_size
                    x2 = min(x1 + self.cell_size, w)
                    y2 = min(y1 + self.cell_size, h)
                    cell = image[y1:y2, x1:x2]
                    cell_hash = self._quick_hash(cell)
                    self.prev_hashes[(col, row)] = cell_hash
                    changed.append((x1, y1, x2 - x1, y2 - y1, cell.copy()))

        return changed

    def merge_adjacent_cells(self, cells: list, full_image: np.ndarray) -> list:
        """Merge adjacent changed grid cells into larger regions via BFS.

        cells: list of (x, y, w, h, cell_img) from diff()
        full_image: the full screen capture (H, W, C)
        Returns: list of (x, y, w, h, region_img) with adjacent cells merged.
        """
        if not cells:
            return []

        # Build set of changed cell grid positions
        changed = set()
        for x, y, w, h, _ in cells:
            col = x // self.cell_size
            row = y // self.cell_size
            changed.add((col, row))

        visited = set()
        merged = []

        for start_col, start_row in changed:
            if (start_col, start_row) in visited:
                continue

            # BFS to find connected component (4-directional)
            stack = [(start_col, start_row)]
            visited.add((start_col, start_row))
            component = [(start_col, start_row)]

            while stack:
                col, row = stack.pop()
                for dc, dr in [(1, 0), (-1, 0), (0, 1), (0, -1)]:
                    neighbor = (col + dc, row + dr)
                    if neighbor in changed and neighbor not in visited:
                        visited.add(neighbor)
                        stack.append(neighbor)
                        component.append(neighbor)

            # Compute bounding box in pixels
            min_col = min(c for c, r in component)
            max_col = max(c for c, r in component)
            min_row = min(r for c, r in component)
            max_row = max(r for c, r in component)

            x1 = min_col * self.cell_size
            y1 = min_row * self.cell_size
            x2 = min((max_col + 1) * self.cell_size, full_image.shape[1])
            y2 = min((max_row + 1) * self.cell_size, full_image.shape[0])

            region_img = full_image[y1:y2, x1:x2]
            merged.append((x1, y1, x2 - x1, y2 - y1, region_img))

        return merged

    def _quick_hash(self, cell: np.ndarray):
        if cell.size == 0:
            return b""
        step_h = max(1, cell.shape[0] // 8)
        step_w = max(1, cell.shape[1] // 8)
        small = cell[::step_h, ::step_w]
        return hashlib.md5(small.tobytes()).digest()
