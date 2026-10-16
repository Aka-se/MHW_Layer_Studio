/* Owned host; deliberately NOT named MonsterHunterWorld.exe. */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdio.h>
#include <string.h>
static const char* dll_path;
static DWORD WINAPI load(LPVOID unused) {
    (void)unused;
    HMODULE dll = LoadLibraryA(dll_path);
    if (!dll) { printf("LoadLibrary failed %lu\n", GetLastError()); return 1; }
    if (!GetProcAddress(dll, "MHWI_VisualControllerExtended_IsInstalled")) return 2;
    return 0; /* Threads are terminated only by owned process exit. */
}
static DWORD WINAPI empty(LPVOID unused) { (void)unused; return 0; }
int main(int argc, char** argv) {
    if (argc != 3) return 2;
    SetErrorMode(SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX);
    dll_path = argv[1];
    if (!strcmp(argv[2], "small-load")) {
        HANDLE t = CreateThread(NULL, 65536, load, NULL, STACK_SIZE_PARAM_IS_A_RESERVATION, NULL);
        DWORD result = 3;
        if (!t || WaitForSingleObject(t, 10000) != WAIT_OBJECT_0 || !GetExitCodeThread(t, &result)) return 4;
        CloseHandle(t);
        if (result) return (int)result;
    } else if (!strcmp(argv[2], "notify")) {
        if (load(NULL)) return 5;
        for (int i = 0; i < 100; ++i) {
            HANDLE t = CreateThread(NULL, 65536, empty, NULL, STACK_SIZE_PARAM_IS_A_RESERVATION, NULL);
            if (!t || WaitForSingleObject(t, 10000) != WAIT_OBJECT_0) return 6;
            CloseHandle(t);
        }
    } else return 2;
    Sleep(100);
    puts("DLL load/TLS owned-host PASS");
    return 0;
}
