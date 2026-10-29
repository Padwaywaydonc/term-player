# Live backdrop window for `play --tombola` on Windows Terminal.
#
# A borderless window that sits directly behind the Windows Terminal window
# that launched it (same position and size, one step lower in z-order). With
# the terminal's background opacity turned down, the scene shows through
# behind every pane. Frames arrive on stdin as drawing commands:
#
#   S ex ey                     world half-extents to fit (start of a frame)
#   L r g b a w x y x y ...     polyline, w = width in px
#   F r g b a x y x y ...       filled polygon
#   C r g b a x y radius        filled circle (radius in world units)
#   E                           end of frame
#
# Coordinates are world units (polygon circumradius 1, y up). EOF closes the
# window. -Snapshot <png> renders the first frame to a file instead of showing.
param([string]$Snapshot = "", [int]$Width = 1200, [int]$Height = 700)

Add-Type -ReferencedAssemblies System.Windows.Forms, System.Drawing -TypeDefinition @"
using System;
using System.Collections.Generic;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Globalization;
using System.Runtime.InteropServices;
using System.Threading;
using System.Windows.Forms;

public class TombolaBackdrop : Form {
    [DllImport("user32.dll")] static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] static extern bool GetWindowRect(IntPtr h, out RECT r);
    [DllImport("user32.dll")] static extern bool IsIconic(IntPtr h);
    [DllImport("user32.dll")] static extern bool IsWindow(IntPtr h);
    [DllImport("user32.dll")] static extern bool SetWindowPos(IntPtr h, IntPtr after, int x, int y, int cx, int cy, uint flags);
    [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
    struct RECT { public int L, T, R, B; }
    const uint SWP_NOACTIVATE = 0x10;
    static readonly CultureInfo Inv = CultureInfo.InvariantCulture;
    public static readonly Color Bg = Color.FromArgb(12, 12, 12);

    IntPtr target;
    volatile string[] frame = new string[0];

    public TombolaBackdrop() {
        target = GetForegroundWindow();          // the terminal the user launched tombola from
        FormBorderStyle = FormBorderStyle.None;
        ShowInTaskbar = false;
        StartPosition = FormStartPosition.Manual;
        BackColor = Bg;
        DoubleBuffered = true;
        var timer = new System.Windows.Forms.Timer { Interval = 33 };
        timer.Tick += (s, e) => Follow();
        timer.Start();
        new Thread(ReadFrames) { IsBackground = true }.Start();
    }

    protected override bool ShowWithoutActivation { get { return true; } }

    protected override CreateParams CreateParams {
        get {
            var cp = base.CreateParams;
            cp.ExStyle |= 0x80 | 0x08000000;     // WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE: no taskbar, no focus
            return cp;
        }
    }

    void Follow() {
        if (!IsWindow(target)) { Close(); return; }
        if (IsIconic(target)) { if (Visible) Hide(); return; }
        if (!Visible) Show();
        RECT r;
        GetWindowRect(target, out r);
        SetWindowPos(Handle, target, r.L, r.T, r.R - r.L, r.B - r.T, SWP_NOACTIVATE);   // just below the terminal
        Invalidate();
    }

    void ReadFrames() {
        var lines = new List<string>();
        string line;
        while ((line = Console.In.ReadLine()) != null) {
            if (line == "E") { frame = lines.ToArray(); lines.Clear(); }
            else lines.Add(line);
        }
        try { BeginInvoke((MethodInvoker)Close); } catch (InvalidOperationException) { }
    }

    protected override void OnPaint(PaintEventArgs e) { Draw(e.Graphics, ClientSize, frame); }

    public static void Draw(Graphics g, Size size, string[] cmds) {
        g.SmoothingMode = SmoothingMode.AntiAlias;
        g.Clear(Bg);
        float scale = 1, cx = size.Width / 2f, cy = size.Height / 2f;
        foreach (var cmd in cmds) {
            var p = cmd.Split(' ');
            if (p[0] == "S") {
                float ex = F(p[1]), ey = F(p[2]);
                scale = 0.92f * Math.Min(size.Width / (2 * ex), size.Height / (2 * ey));
                continue;
            }
            var col = Color.FromArgb(I(p[4]), I(p[1]), I(p[2]), I(p[3]));
            if (p[0] == "C") {
                float x = cx + F(p[5]) * scale, y = cy - F(p[6]) * scale, rad = Math.Max(1.5f, F(p[7]) * scale);
                using (var b = new SolidBrush(col)) g.FillEllipse(b, x - rad, y - rad, 2 * rad, 2 * rad);
                continue;
            }
            int first = p[0] == "L" ? 6 : 5;
            var pts = new PointF[(p.Length - first) / 2];
            for (int i = 0; i < pts.Length; i++)
                pts[i] = new PointF(cx + F(p[first + 2 * i]) * scale, cy - F(p[first + 2 * i + 1]) * scale);
            if (pts.Length < 2) continue;
            if (p[0] == "L") {
                using (var pen = new Pen(col, F(p[5])) { LineJoin = LineJoin.Round, StartCap = LineCap.Round, EndCap = LineCap.Round })
                    g.DrawLines(pen, pts);
            } else if (p[0] == "F") {
                using (var b = new SolidBrush(col)) g.FillPolygon(b, pts);
            }
        }
    }

    static float F(string s) { return float.Parse(s, Inv); }
    static int I(string s) { return int.Parse(s, Inv); }
}
"@

[TombolaBackdrop]::SetProcessDPIAware() | Out-Null
if ($Snapshot) {
    $cmds = New-Object System.Collections.Generic.List[string]
    while (($line = [Console]::In.ReadLine()) -ne $null -and $line -ne "E") { $cmds.Add($line) }
    $bmp = New-Object System.Drawing.Bitmap $Width, $Height
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    [TombolaBackdrop]::Draw($g, (New-Object System.Drawing.Size $Width, $Height), $cmds.ToArray())
    $bmp.Save($Snapshot)
    exit 0
}
[System.Windows.Forms.Application]::Run((New-Object TombolaBackdrop))
