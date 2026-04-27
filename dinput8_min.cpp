#include <windows.h>
#include <cstdio>
#include <cstdarg>
#include <cstring>

using DirectInput8CreateFn = HRESULT (WINAPI*)(HINSTANCE, DWORD, REFIID, LPVOID*, LPUNKNOWN);
using CreateFontIndirectAFn = HFONT (WINAPI*)(const LOGFONTA*);
using CreateFontIndirectWFn = HFONT (WINAPI*)(const LOGFONTW*);

static HMODULE g_real_dinput8 = nullptr;
static DirectInput8CreateFn g_real_direct_input8_create = nullptr;
static CreateFontIndirectAFn g_real_create_font_indirect_a = nullptr;
static CreateFontIndirectWFn g_real_create_font_indirect_w = nullptr;
static volatile LONG g_hook_ready = 0;
static const char* kBuildTag = "min_font_only_v1_20260425_2154";

static void log_line(const char* fmt, ...) {
    char path[MAX_PATH] = {0};
    GetModuleFileNameA(nullptr, path, MAX_PATH);
    char* slash = strrchr(path, '\\');
    if (slash) *(slash + 1) = '\0';
    strcat(path, "dinput8_min_font.log");

    FILE* fp = fopen(path, "a");
    if (!fp) return;

    SYSTEMTIME st{};
    GetLocalTime(&st);
    fprintf(fp, "[%04u-%02u-%02u %02u:%02u:%02u] ",
            st.wYear, st.wMonth, st.wDay, st.wHour, st.wMinute, st.wSecond);

    va_list args;
    va_start(args, fmt);
    vfprintf(fp, fmt, args);
    va_end(args);
    fprintf(fp, "\n");
    fclose(fp);
}

static bool write_ptr(void** where, void* value) {
    DWORD old = 0;
    if (!VirtualProtect(where, sizeof(void*), PAGE_READWRITE, &old)) return false;
    *where = value;
    DWORD tmp = 0;
    VirtualProtect(where, sizeof(void*), old, &tmp);
    return true;
}

static bool patch_iat(HMODULE module, const char* imported_dll, const char* imported_name, void* replacement, void** original_out) {
    if (!module) return false;
    auto base = reinterpret_cast<unsigned char*>(module);
    auto dos = reinterpret_cast<IMAGE_DOS_HEADER*>(base);
    if (dos->e_magic != IMAGE_DOS_SIGNATURE) return false;
    auto nt = reinterpret_cast<IMAGE_NT_HEADERS*>(base + dos->e_lfanew);
    if (nt->Signature != IMAGE_NT_SIGNATURE) return false;

    auto& dir = nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT];
    if (!dir.VirtualAddress || !dir.Size) return false;

    auto imp = reinterpret_cast<IMAGE_IMPORT_DESCRIPTOR*>(base + dir.VirtualAddress);
    for (; imp->Name; ++imp) {
        const char* dll_name = reinterpret_cast<const char*>(base + imp->Name);
        if (!dll_name || _stricmp(dll_name, imported_dll) != 0) continue;

        auto thunk = reinterpret_cast<IMAGE_THUNK_DATA*>(base + imp->FirstThunk);
        auto orig_thunk = reinterpret_cast<IMAGE_THUNK_DATA*>(
            base + (imp->OriginalFirstThunk ? imp->OriginalFirstThunk : imp->FirstThunk));

        for (; orig_thunk->u1.AddressOfData; ++orig_thunk, ++thunk) {
            if (orig_thunk->u1.Ordinal & IMAGE_ORDINAL_FLAG) continue;
            auto ibn = reinterpret_cast<IMAGE_IMPORT_BY_NAME*>(base + orig_thunk->u1.AddressOfData);
            const char* fn = reinterpret_cast<const char*>(ibn->Name);
            if (!fn || strcmp(fn, imported_name) != 0) continue;

            void* cur = reinterpret_cast<void*>(thunk->u1.Function);
            if (original_out) *original_out = cur;
            return write_ptr(reinterpret_cast<void**>(&thunk->u1.Function), replacement);
        }
    }
    return false;
}

static HFONT WINAPI hook_CreateFontIndirectA(const LOGFONTA* lplf) {
    if (!lplf) return g_real_create_font_indirect_a ? g_real_create_font_indirect_a(lplf) : nullptr;
    LOGFONTA lf = *lplf;
    BYTE old_cs = lf.lfCharSet;
    bool changed = false;
    if (lf.lfCharSet == ANSI_CHARSET || lf.lfCharSet == DEFAULT_CHARSET || lf.lfCharSet == SHIFTJIS_CHARSET) {
        lf.lfCharSet = GB2312_CHARSET;
        lstrcpynA(lf.lfFaceName, "SimHei", LF_FACESIZE);
        changed = true;
    }
    HFONT f = g_real_create_font_indirect_a ? g_real_create_font_indirect_a(&lf) : nullptr;
    if (changed) {
        log_line("FONT A: charset %u -> %u face=%s", (unsigned)old_cs, (unsigned)lf.lfCharSet, lf.lfFaceName);
    }
    return f;
}

static HFONT WINAPI hook_CreateFontIndirectW(const LOGFONTW* lplf) {
    if (!lplf) return g_real_create_font_indirect_w ? g_real_create_font_indirect_w(lplf) : nullptr;
    LOGFONTW lf = *lplf;
    BYTE old_cs = lf.lfCharSet;
    bool changed = false;
    if (lf.lfCharSet == ANSI_CHARSET || lf.lfCharSet == DEFAULT_CHARSET || lf.lfCharSet == SHIFTJIS_CHARSET) {
        lf.lfCharSet = GB2312_CHARSET;
        lstrcpynW(lf.lfFaceName, L"SimHei", LF_FACESIZE);
        changed = true;
    }
    HFONT f = g_real_create_font_indirect_w ? g_real_create_font_indirect_w(&lf) : nullptr;
    if (changed) {
        log_line("FONT W: charset %u -> %u", (unsigned)old_cs, (unsigned)lf.lfCharSet);
    }
    return f;
}

static void setup_font_hooks_only() {
    if (InterlockedCompareExchange(&g_hook_ready, 1, 0) != 0) return;

    HMODULE exe = GetModuleHandleA(nullptr);
    HMODULE gdi = GetModuleHandleA("gdi32.dll");
    if (!gdi) gdi = LoadLibraryA("gdi32.dll");
    if (!exe || !gdi) {
        log_line("Font-only setup failed: exe=%p gdi=%p", exe, gdi);
        return;
    }

    g_real_create_font_indirect_a = reinterpret_cast<CreateFontIndirectAFn>(GetProcAddress(gdi, "CreateFontIndirectA"));
    g_real_create_font_indirect_w = reinterpret_cast<CreateFontIndirectWFn>(GetProcAddress(gdi, "CreateFontIndirectW"));

    void* orig_a = nullptr;
    bool ok_a = patch_iat(exe, "GDI32.dll", "CreateFontIndirectA", (void*)&hook_CreateFontIndirectA, &orig_a);
    if (ok_a && orig_a) g_real_create_font_indirect_a = (CreateFontIndirectAFn)orig_a;
    log_line("IAT hook Twain.exe CreateFontIndirectA: %s (orig=%p)", ok_a ? "OK" : "FAIL", orig_a);

    void* orig_w = nullptr;
    bool ok_w = patch_iat(exe, "GDI32.dll", "CreateFontIndirectW", (void*)&hook_CreateFontIndirectW, &orig_w);
    if (ok_w && orig_w) g_real_create_font_indirect_w = (CreateFontIndirectWFn)orig_w;
    log_line("IAT hook Twain.exe CreateFontIndirectW: %s (orig=%p)", ok_w ? "OK" : "FAIL", orig_w);
}

static bool load_real_dinput8() {
    if (g_real_direct_input8_create) return true;

    char sys_dir[MAX_PATH] = {0};
    UINT len = GetSystemDirectoryA(sys_dir, MAX_PATH);
    if (len == 0 || len >= MAX_PATH) {
        log_line("GetSystemDirectoryA failed: %lu", GetLastError());
        return false;
    }

    strcat(sys_dir, "\\dinput8.dll");
    g_real_dinput8 = LoadLibraryA(sys_dir);
    if (!g_real_dinput8) {
        log_line("LoadLibraryA(%s) failed: %lu", sys_dir, GetLastError());
        return false;
    }

    g_real_direct_input8_create = reinterpret_cast<DirectInput8CreateFn>(
        GetProcAddress(g_real_dinput8, "DirectInput8Create"));
    if (!g_real_direct_input8_create) {
        log_line("GetProcAddress(DirectInput8Create) failed: %lu", GetLastError());
        return false;
    }

    log_line("Loaded real dinput8.dll from %s", sys_dir);
    return true;
}

extern "C" __declspec(dllexport)
HRESULT WINAPI DirectInput8Create(HINSTANCE hinst, DWORD dwVersion, REFIID riidltf, LPVOID* ppvOut, LPUNKNOWN punkOuter) {
    if (!load_real_dinput8()) return E_FAIL;
    return g_real_direct_input8_create(hinst, dwVersion, riidltf, ppvOut, punkOuter);
}

BOOL WINAPI DllMain(HINSTANCE hinst, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(hinst);
        log_line("dinput8_min_font injected. build_tag=%s", kBuildTag);
        load_real_dinput8();
        setup_font_hooks_only();
    }
    return TRUE;
}

