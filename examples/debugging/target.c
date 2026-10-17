#include <stdio.h>
volatile int counter = 42;
int main(void) { printf("counter=%d\n", counter); return 0; }
