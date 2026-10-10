/* 用 libXcursor 官方 API 验证主题能否按名字加载（不需要 X server） */
#include <X11/Xcursor/Xcursor.h>
#include <stdio.h>

int main(int argc, char **argv) {
    const char *theme = argv[1];
    const char *names[] = {"left_ptr","default","text","hand2","wait","progress",
                           "crosshair","not-allowed","ew-resize","ns-resize",
                           "nwse-resize","nesw-resize","move","grab","help",NULL};
    int size = 24, ok = 0, total = 0;
    printf("主题 \"%s\"  (请求尺寸 %d)\n", theme, size);
    for (int i = 0; names[i]; i++) {
        total++;
        XcursorImage *img = XcursorLibraryLoadImage(names[i], theme, size);
        if (img) {
            printf("  \033[32m✓\033[0m %-14s %dx%d  热点(%u,%u)\n",
                   names[i], img->width, img->height, img->xhot, img->yhot);
            XcursorImageDestroy(img); ok++;
        } else {
            printf("  \033[31m✘\033[0m %-14s 加载失败\n", names[i]);
        }
    }
    /* 尺寸适配检查：请求 48 时是否给出 48 的图 */
    XcursorImage *big = XcursorLibraryLoadImage("left_ptr", theme, 48);
    if (big) { printf("  尺寸 48 请求 -> 实际 %dx%d\n", big->width, big->height); XcursorImageDestroy(big); }
    printf("\n结果: %d/%d 个名字成功加载\n", ok, total);
    return ok == total ? 0 : 1;
}
