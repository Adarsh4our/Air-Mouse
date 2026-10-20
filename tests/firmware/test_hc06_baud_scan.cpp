#include "../../firmware/hc06_baud_scan/hc06_baud_scan.ino"

int main() {
  setup();
  assert(hc06.baud == 9600);
  for (unsigned int pass = 0; pass < 2; pass++) {
    for (unsigned int index = 0; index < sizeof(BAUDS) / sizeof(BAUDS[0]); index++) {
      assert(hc06.baud == (int)BAUDS[index]);
      loop();
      auto first = hc06.packets.back();
      char expected[24];
      snprintf(expected, sizeof(expected), "\nBAUD:%lu HELLO:", (unsigned long)BAUDS[index]);
      assert(first.find(expected) == 0);
      assert(first.back() == '\n');
      auto count = hc06.packets.size();
      loop();
      assert(hc06.packets.size() == count); // No hot-loop flooding.
      fakeMillis = changedAt + 500;
      loop();
      assert(hc06.packets.size() == count + 1);
      fakeMillis = changedAt + DWELL_MS;
      loop();
    }
  }
  assert(hc06.baud == 9600); // Scan repeats for connections made after startup.
  for (const auto& packet : hc06.packets) {
    assert(packet.find("AT") == std::string::npos); // No module configuration commands.
  }
  puts("Baud scan behavior checks passed.");
}
