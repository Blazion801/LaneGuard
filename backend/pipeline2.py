import cv2
import numpy as np
import time

# ---------------------------------------------------------------------------
# Real-world scale constants (BEV pixel space, 1280x720)
# ---------------------------------------------------------------------------
YM_PER_PIX = 30.0 / 720   # metres per pixel in y
XM_PER_PIX =  3.7 / 700   # metres per pixel in x (US standard lane width)


# ---------------------------------------------------------------------------
# Stage 1 — Camera calibration (optional)
# ---------------------------------------------------------------------------

def calibrate_camera(calib_image_paths, board_size=(9, 6)):
    """
    Compute camera matrix K and distortion coefficients D from chessboard
    calibration images.  Returns (K, D), or (None, None) if no valid images
    are found.  Pass the result to ImprovedLaneDetector(K=K, D=D).
    """
    objp = np.zeros((board_size[0] * board_size[1], 3), np.float32)
    objp[:, :2] = np.mgrid[0:board_size[0], 0:board_size[1]].T.reshape(-1, 2)

    obj_points, img_points = [], []

    for path in calib_image_paths:
        img = cv2.imread(path)
        if img is None:
            continue
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        ret, corners = cv2.findChessboardCorners(gray, board_size, None)
        if ret:
            obj_points.append(objp)
            corners_refined = cv2.cornerSubPix(
                gray, corners, (11, 11), (-1, -1),
                criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_MAX_ITER, 30, 0.001)
            )
            img_points.append(corners_refined)

    if not obj_points:
        print("[CALIBRATION] No valid chessboard images found. Skipping undistortion.")
        return None, None

    h, w = cv2.imread(calib_image_paths[0]).shape[:2]
    ret, K, D, _, _ = cv2.calibrateCamera(obj_points, img_points, (w, h), None, None)
    print(f"[CALIBRATION] RMS reprojection error: {ret:.4f} px")
    return K, D


def undistort_frame(frame, K, D):
    """Remove lens distortion. Returns frame unchanged if K/D are None."""
    if K is None or D is None:
        return frame
    return cv2.undistort(frame, K, D)


# ---------------------------------------------------------------------------
# Stage 2 — Region of Interest
# ---------------------------------------------------------------------------

def apply_roi(frame, y_top=400, y_bottom=650):
    """
    Mask a trapezoidal ROI covering the road surface.
    Works on both grayscale (2-D) and BGR (3-D) frames.
    Tuned for 1280x720 input.
    """
    h, w = frame.shape[:2]
    mask = np.zeros((h, w), dtype=np.uint8)

    trap = np.array([[
        (int(w * 0.10), y_bottom),
        (int(w * 0.44), y_top),
        (int(w * 0.56), y_top),
        (int(w * 0.90), y_bottom),
    ]], dtype=np.int32)

    cv2.fillPoly(mask, trap, 255)

    if frame.ndim == 3:
        return cv2.bitwise_and(frame, frame, mask=mask)
    return cv2.bitwise_and(frame, mask)


# ---------------------------------------------------------------------------
# Stage 3 — Inverse Perspective Mapping (Bird's Eye View)
# ---------------------------------------------------------------------------

def build_homography(img_size=(1280, 720)):
    """
    Compute homography H (perspective -> BEV) and H_inv (BEV -> perspective).
    Source points are calibrated for 1280x720 road images.
    Returns (H, H_inv, src_pts, dst_pts).
    """
    w, h = img_size

    src = np.float32([
        [w * 0.44, h * 0.63],   # top-left
        [w * 0.56, h * 0.63],   # top-right
        [w * 0.15, h * 0.94],   # bottom-left
        [w * 0.85, h * 0.94],   # bottom-right
    ])

    dst = np.float32([
        [w * 0.25, 0],
        [w * 0.75, 0],
        [w * 0.25, h],
        [w * 0.75, h],
    ])

    H,     _ = cv2.findHomography(src, dst)
    H_inv, _ = cv2.findHomography(dst, src)
    return H, H_inv, src, dst


def warp_to_bev(frame, H, img_size=(1280, 720)):
    """Perspective view -> Bird's Eye View."""
    return cv2.warpPerspective(frame, H, img_size)


def unwarp_points(pts, H_inv):
    """
    Map (N, 2) points from BEV space back to original perspective space.
    """
    pts_h = np.hstack([pts, np.ones((len(pts), 1), dtype=np.float32)])
    proj  = (H_inv @ pts_h.T).T
    proj /= proj[:, 2:3]
    return proj[:, :2]


# ---------------------------------------------------------------------------
# Stage 4 — HLS + Sobel binary feature extraction
# ---------------------------------------------------------------------------

def extract_lane_binary(frame, s_thresh=(170, 255), sx_thresh=(20, 100)):
    """
    Produce a binary image highlighting lane pixels.

    Two complementary masks are combined with a bitwise OR:
      - HLS S-channel threshold  : robust to shadows and lighting variation.
      - Sobel-x gradient on L    : detects vertical lane-line edges.
    """
    hls       = cv2.cvtColor(frame, cv2.COLOR_BGR2HLS)
    s_channel = hls[:, :, 2].astype(np.float32)
    l_channel = hls[:, :, 1].astype(np.float32)

    s_binary = np.zeros(s_channel.shape, dtype=np.uint8)
    s_binary[(s_channel >= s_thresh[0]) & (s_channel <= s_thresh[1])] = 255

    sobelx     = cv2.Sobel(l_channel, cv2.CV_64F, 1, 0, ksize=3)
    abs_sobelx = np.absolute(sobelx)
    scaled     = np.uint8(255 * abs_sobelx / (np.max(abs_sobelx) + 1e-6))
    sx_binary  = np.zeros_like(scaled)
    sx_binary[(scaled >= sx_thresh[0]) & (scaled <= sx_thresh[1])] = 255

    return cv2.bitwise_or(s_binary, sx_binary)


# ---------------------------------------------------------------------------
# Stage 5 — Sliding-window polynomial fit
# ---------------------------------------------------------------------------

def sliding_window_fit(binary_warped, n_windows=9, margin=100, min_pix=50):
    """
    Detect lane pixels via sliding windows and fit a 2nd-order polynomial
    x = A*y^2 + B*y + C to each lane.

    Returns:
      left_fit  : np.ndarray [A, B, C] or None
      right_fit : np.ndarray [A, B, C] or None
      debug_img : BGR visualisation of windows and fitted curves
    """
    h, w = binary_warped.shape

    histogram  = np.sum(binary_warped[h // 2:, :], axis=0)
    midpoint   = w // 2
    left_base  = int(np.argmax(histogram[:midpoint]))
    right_base = int(np.argmax(histogram[midpoint:]) + midpoint)

    nonzero_y, nonzero_x = binary_warped.nonzero()
    win_h = h // n_windows

    left_x,  left_y  = [], []
    right_x, right_y = [], []
    lx_cur, rx_cur   = left_base, right_base

    debug = cv2.cvtColor(binary_warped, cv2.COLOR_GRAY2BGR)

    for win in range(n_windows):
        y_lo = h - (win + 1) * win_h
        y_hi = h - win * win_h
        lx_lo, lx_hi = lx_cur - margin, lx_cur + margin
        rx_lo, rx_hi = rx_cur - margin, rx_cur + margin

        cv2.rectangle(debug, (lx_lo, y_lo), (lx_hi, y_hi), (0, 255, 160), 2)
        cv2.rectangle(debug, (rx_lo, y_lo), (rx_hi, y_hi), (255, 107,  53), 2)

        good_l = np.where(
            (nonzero_y >= y_lo) & (nonzero_y < y_hi) &
            (nonzero_x >= lx_lo) & (nonzero_x < lx_hi)
        )[0]
        good_r = np.where(
            (nonzero_y >= y_lo) & (nonzero_y < y_hi) &
            (nonzero_x >= rx_lo) & (nonzero_x < rx_hi)
        )[0]

        left_x.extend(nonzero_x[good_l]);  left_y.extend(nonzero_y[good_l])
        right_x.extend(nonzero_x[good_r]); right_y.extend(nonzero_y[good_r])

        if len(good_l) >= min_pix:
            lx_cur = int(np.mean(nonzero_x[good_l]))
        if len(good_r) >= min_pix:
            rx_cur = int(np.mean(nonzero_x[good_r]))

    left_fit  = np.polyfit(left_y,  left_x,  2) if len(left_y)  >= 10 else None
    right_fit = np.polyfit(right_y, right_x, 2) if len(right_y) >= 10 else None

    y_pts = np.linspace(0, h - 1, h)
    for fit, color in [(left_fit, (0, 255, 160)), (right_fit, (255, 107, 53))]:
        if fit is not None:
            x_pts = np.polyval(fit, y_pts).astype(int)
            pts   = np.column_stack([x_pts, y_pts.astype(int)])
            valid = (x_pts >= 0) & (x_pts < w)
            if valid.sum() > 2:
                cv2.polylines(debug, [pts[valid].reshape(-1, 1, 2)], False, color, 3)

    return left_fit, right_fit, debug


# ---------------------------------------------------------------------------
# Stage 6 — Curvature, lateral offset, alert
# ---------------------------------------------------------------------------

def compute_curvature(fit, y_eval, ym=YM_PER_PIX, xm=XM_PER_PIX):
    """
    Radius of curvature in metres.  Returns inf for straight roads or
    when no lane is detected.
    """
    if fit is None:
        return float('inf')
    A, B, _ = fit
    A_r = A * xm / ym ** 2
    B_r = B * xm / ym
    y_r = y_eval * ym
    return ((1 + (2 * A_r * y_r + B_r) ** 2) ** 1.5) / (abs(2 * A_r) + 1e-6)


def compute_lateral_offset(left_fit, right_fit, img_width, img_height,
                            xm=XM_PER_PIX):
    """
    Vehicle lateral offset from lane centre in metres.
    Positive = vehicle is right of centre.
    Returns 0.0 if either lane is undetected.
    """
    if left_fit is None or right_fit is None:
        return 0.0
    y_bottom    = img_height - 1
    left_x      = np.polyval(left_fit,  y_bottom)
    right_x     = np.polyval(right_fit, y_bottom)
    lane_centre = (left_x + right_x) / 2.0
    img_centre  = img_width / 2.0
    return (img_centre - lane_centre) * xm


def classify_alert(lateral_offset):
    """
    DEPARTURE  : |offset| > 0.6 m  (critical)
    DRIFT      : |offset| > 0.3 m  (warning)
    OK         : otherwise
    """
    abs_off = abs(lateral_offset)
    if abs_off > 0.6:
        return "DEPARTURE"
    elif abs_off > 0.3:
        return "DRIFT"
    return "OK"


# ---------------------------------------------------------------------------
# Stage 7 — Visualisation
# ---------------------------------------------------------------------------

def draw_lane_overlay(original, binary_warped, left_fit, right_fit,
                      H_inv, lateral_offset):
    """
    Draw filled lane polygon and lane lines onto the original frame.
    Polygon is drawn in BEV space then unwarped back to perspective.
    Fill colour reflects alert level (green / orange / red).
    """
    h, w    = binary_warped.shape
    overlay = np.zeros((h, w, 3), dtype=np.uint8)
    y_pts   = np.linspace(0, h - 1, h)

    abs_off    = abs(lateral_offset)
    fill_color = (
        (0, 0, 200)   if abs_off > 0.6 else
        (0, 180, 255) if abs_off > 0.3 else
        (0, 200, 100)
    )

    if left_fit is not None and right_fit is not None:
        left_x    = np.polyval(left_fit,  y_pts)
        right_x   = np.polyval(right_fit, y_pts)
        pts_left  = np.column_stack([left_x,        y_pts       ])
        pts_right = np.column_stack([right_x[::-1], y_pts[::-1] ])
        pts_all   = np.vstack([pts_left, pts_right]).astype(np.int32)
        cv2.fillPoly(overlay, [pts_all.reshape(-1, 1, 2)], fill_color)

    img_size = (original.shape[1], original.shape[0])
    unwarped = cv2.warpPerspective(overlay, H_inv, img_size)
    output   = cv2.addWeighted(original, 1.0, unwarped, 0.35, 0)

    oh, ow = original.shape[:2]
    for fit, color in [(left_fit, (0, 255, 160)), (right_fit, (255, 107, 53))]:
        if fit is None:
            continue
        x_pts    = np.polyval(fit, y_pts)
        bev_pts  = np.column_stack([x_pts, y_pts]).astype(np.float32)
        orig_pts = unwarp_points(bev_pts, H_inv).astype(np.int32)
        valid    = (
            (orig_pts[:, 0] >= 0) & (orig_pts[:, 0] < ow) &
            (orig_pts[:, 1] >= 0) & (orig_pts[:, 1] < oh)
        )
        if valid.sum() > 2:
            cv2.polylines(output, [orig_pts[valid].reshape(-1, 1, 2)],
                          False, color, 4, cv2.LINE_AA)

    return output


def draw_hud(frame, left_fit, right_fit, lateral_offset, curvature, alert, fps):
    """Overlay HUD metrics and alert banner onto the frame."""
    out  = frame.copy()
    h, w = out.shape[:2]
    font = cv2.FONT_HERSHEY_SIMPLEX

    green  = (0, 229, 160)
    yellow = (80, 200, 255)
    red    = (80,  80, 240)
    muted  = (120, 120, 120)
    white  = (220, 220, 220)

    cv2.rectangle(out, (10, 10), (390, 175), (20, 20, 20), -1)
    cv2.rectangle(out, (10, 10), (390, 175), (60, 60, 60),  1)

    abs_off    = abs(lateral_offset)
    off_color  = red if abs_off > 0.6 else yellow if abs_off > 0.3 else green
    radius_str = f"{curvature:.0f} m" if curvature < 9000 else "Straight"
    l_det      = left_fit  is not None
    r_det      = right_fit is not None

    hud_items = [
        ("LANE GUARD  [IMPROVED]",               (20, 38),  0.60, green,     2),
        (f"{fps:.1f} FPS",                        (300, 38), 0.50, muted,     1),
        (f"Curvature : {radius_str}",             (20, 68),  0.48, white,     1),
        (f"Offset    : {lateral_offset:+.2f} m", (20, 93),  0.48, off_color, 1),
        (f"Alert     : {alert}",                 (20, 118), 0.48, off_color, 1),
        (f"L-lane: {'OK' if l_det else 'MISS'}   R-lane: {'OK' if r_det else 'MISS'}",
                                                 (20, 148), 0.42, muted,     1),
    ]
    for text, pos, scale, color, thick in hud_items:
        cv2.putText(out, text, pos, font, scale, color, thick, cv2.LINE_AA)

    if alert == "DEPARTURE":
        cv2.rectangle(out, (0, h - 52), (w, h), (0, 0, 170), -1)
        cv2.putText(out, "  LANE DEPARTURE  -  IMMEDIATE ACTION REQUIRED",
                    (10, h - 18), font, 0.58, (255, 255, 255), 2, cv2.LINE_AA)
    elif alert == "DRIFT":
        cv2.rectangle(out, (0, h - 52), (w, h), (0, 120, 210), -1)
        cv2.putText(out, "  LANE DRIFT DETECTED  -  CORRECTION RECOMMENDED",
                    (10, h - 18), font, 0.55, (255, 255, 255), 1, cv2.LINE_AA)

    return out


# ---------------------------------------------------------------------------
# Main pipeline class
# ---------------------------------------------------------------------------

class ImprovedLaneDetector:
    """
    End-to-end improved lane detection pipeline.

    Stages:
      1. Undistort          — optional, requires calibrate_camera() first
      2. ROI mask           — exclude sky and vehicle hood
      3. IPM                — Bird's Eye View via homography
      4. HLS + Sobel binary — colour + gradient feature extraction
      5. Sliding window     — polynomial fit  x = A*y^2 + B*y + C
      6. Curvature, offset, alert classification
      7. Visualisation      — unwarped lane overlay + HUD

    Usage (no calibration):
        detector = ImprovedLaneDetector()
        output, results = detector.process_frame(frame)

    Usage (with calibration):
        K, D = calibrate_camera(glob.glob("calib_images/*.jpg"))
        detector = ImprovedLaneDetector(K=K, D=D)
        output, results = detector.process_frame(frame)
    """

    def __init__(self, img_size=(1280, 720), K=None, D=None):
        self.img_size            = img_size
        self.K                   = K
        self.D                   = D
        self.H, self.H_inv, _, _ = build_homography(img_size)
        self._t_prev             = time.perf_counter()
        self._left_smooth        = None
        self._right_smooth       = None
        self._alpha              = 0.85   # EMA weight — higher = smoother

    def _smooth(self, new_fit, prev_fit):
        """Exponential moving average over polynomial coefficients."""
        if new_fit is None:
            return prev_fit
        if prev_fit is None:
            return new_fit
        return self._alpha * prev_fit + (1 - self._alpha) * new_fit

    def process_frame(self, frame, show_debug=False):
        """
        Run the full pipeline on a single BGR frame.

        Returns:
          output  : annotated BGR frame (same resolution as img_size)
          results : dict with keys left_fit, right_fit, curvature,
                    offset, alert, fps
        """
        frame = cv2.resize(frame, self.img_size)

        # 1. Undistort
        undist = undistort_frame(frame, self.K, self.D)

        # 2. ROI
        roi = apply_roi(undist)

        # 3. Bird's Eye View
        warped = warp_to_bev(roi, self.H, self.img_size)

        # 4. Binary feature extraction
        binary = extract_lane_binary(warped)

        # 5. Sliding window polynomial fit
        left_fit, right_fit, debug_bev = sliding_window_fit(binary)

        # Temporal smoothing
        self._left_smooth  = self._smooth(left_fit,  self._left_smooth)
        self._right_smooth = self._smooth(right_fit, self._right_smooth)
        left_fit  = self._left_smooth
        right_fit = self._right_smooth

        # 6. Curvature, offset, alert
        h, w      = frame.shape[:2]
        curv_l    = compute_curvature(left_fit,  h - 1)
        curv_r    = compute_curvature(right_fit, h - 1)
        finite    = [r for r in [curv_l, curv_r] if r < 9000]
        curvature = float(np.mean(finite)) if finite else float('inf')
        offset    = compute_lateral_offset(left_fit, right_fit, w, h)
        alert     = classify_alert(offset)

        now          = time.perf_counter()
        fps          = 1.0 / max(now - self._t_prev, 1e-9)
        self._t_prev = now

        # 7. Visualisation
        output = draw_lane_overlay(frame, binary, left_fit, right_fit,
                                   self.H_inv, offset)
        output = draw_hud(output, left_fit, right_fit, offset,
                          curvature, alert, fps)

        if show_debug and debug_bev is not None:
            small = cv2.resize(debug_bev, (320, 180))
            output[h - 190:h - 10, w - 330:w - 10] = small

        results = {
            "left_fit":  left_fit,
            "right_fit": right_fit,
            "curvature": curvature,
            "offset":    offset,
            "alert":     alert,
            "fps":       fps,
        }
        return output, results


# ---------------------------------------------------------------------------
# Video / webcam runner
# ---------------------------------------------------------------------------

def run_video(source, detector, show_debug=False, save_path=""):
    """
    Run the pipeline on a video file or webcam stream.
    Press 'q' to quit, 'd' to toggle the BEV debug overlay.

    Args:
      source     : file path (str) or webcam index (int)
      detector   : ImprovedLaneDetector instance
      show_debug : show sliding-window BEV in corner overlay
      save_path  : if non-empty, write annotated video to this path
    """
    cap = cv2.VideoCapture(source)
    if not cap.isOpened():
        raise IOError(f"Cannot open source: {source}")

    writer = None
    if save_path:
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(save_path, fourcc, 30, detector.img_size)

    print("[LANE GUARD] Running... press 'q' to quit, 'd' to toggle debug.")
    while True:
        ret, frame = cap.read()
        if not ret:
            break

        output, _ = detector.process_frame(frame, show_debug=show_debug)
        cv2.imshow("Lane Guard - Improved", output)
        if writer:
            writer.write(output)

        key = cv2.waitKey(1) & 0xFF
        if key == ord('q'):
            break
        elif key == ord('d'):
            show_debug = not show_debug

    cap.release()
    if writer:
        writer.release()
    cv2.destroyAllWindows()
    print("[LANE GUARD] Done.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Improved Lane Detection Pipeline")
    parser.add_argument("--source",  default="0",
                        help="Video path or webcam index (default: 0)")
    parser.add_argument("--save",    default="",
                        help="Save annotated output to this path")
    parser.add_argument("--debug",   action="store_true",
                        help="Show BEV sliding-window overlay")
    parser.add_argument("--calib",   nargs="*", default=None,
                        help="Chessboard calibration image paths")
    args = parser.parse_args()

    K, D = None, None
    if args.calib:
        K, D = calibrate_camera(args.calib)

    detector = ImprovedLaneDetector(K=K, D=D)
    source   = int(args.source) if args.source.isdigit() else args.source
    run_video(source, detector, show_debug=args.debug, save_path=args.save)
