// SPDX-License-Identifier: Apache-2.0
// Included in the common runtime: no backend-specific callback or MMIO ABI.
#include <dfv_config.h>
#include <map>
#include <sstream>
#include <limits>

struct dfv_host_state {
  vx_buffer_h buffer = nullptr;
  vx_dfv_config_t config = {};
  bool running = false;
  bool required = false;
};
static std::map<vx_device_h, dfv_host_state> dfv_states;

static bool dfv_number(const std::string& text, uint32_t* value) {
  if (text.empty() || text[0] == '-' || text.find_first_of(" \t\n+") != std::string::npos)
    return false;
  char* end = nullptr;
  errno = 0;
  auto parsed = std::strtoull(text.c_str(), &end, 0);
  if (errno || *end || parsed > std::numeric_limits<uint32_t>::max()) return false;
  *value = uint32_t(parsed);
  return true;
}

static int dfv_initialize(vx_device_h device) {
  dfv_host_state state;
  auto& c = state.config;
  c.magic = VX_DFV_CONFIG_MAGIC;
  c.version = VX_DFV_CONFIG_VERSION;
  c.sequence = 1;
  c.values[2] = 0xabcdef00;
  c.values[3] = 0xe000;
  c.values[7] = 0xe000;
  c.values[8] = 0x87654321;
  c.values[11] = 0x2ed1;
  c.values[12] = 0xffff;
  c.values[14] = 0x1800;
  if (auto env = std::getenv("VORTEX_DFV_CSRS")) {
    std::stringstream stream(env);
    std::string pair;
    uint32_t seen = 0;
    if (!*env) return -1;
    while (std::getline(stream, pair, ',')) {
      auto equal = pair.find('=');
      uint32_t addr, value;
      if (equal == std::string::npos
          || !dfv_number(pair.substr(0, equal), &addr)
          || !dfv_number(pair.substr(equal + 1), &value)
          || addr < VX_DFV_CSR_FIRST || addr >= VX_DFV_CSR_FIRST + VX_DFV_CSR_COUNT
          || !vx_dfv_value_valid(addr - VX_DFV_CSR_FIRST, value)) return -1;
      auto index = addr - VX_DFV_CSR_FIRST;
      if (seen & (1u << index)) return -1;
      seen |= 1u << index;
      c.values[index] = value;
    }
    if (std::string(env).back() == ',') return -1;
    state.required = true;
  }
  if (g_callbacks.mem_reserve(device, VX_DFV_CONFIG_ADDR, sizeof(c),
                              VX_MEM_READ_WRITE, &state.buffer)) return -1;
  if (g_callbacks.copy_to_dev(state.buffer, &c, 0, sizeof(c))) {
    g_callbacks.mem_free(state.buffer);
    return -1;
  }
  static_assert(sizeof(vx_dfv_config_t) == 192, "DFV mailbox layout changed");
  dfv_states.emplace(device, state);
  return 0;
}

extern int vx_dfv_csr_write(vx_device_h device, uint32_t addr, uint32_t value) {
  auto found = dfv_states.find(device);
  if (found == dfv_states.end() || found->second.running
      || addr < VX_DFV_CSR_FIRST || addr >= VX_DFV_CSR_FIRST + VX_DFV_CSR_COUNT
      || !vx_dfv_value_valid(addr - VX_DFV_CSR_FIRST, value)) return -1;
  auto& state = found->second;
  state.config.values[addr - VX_DFV_CSR_FIRST] = value;
  if (++state.config.sequence == 0) ++state.config.sequence;
  state.config.applied = 0;
  state.required = true;
  return 0;
}

extern int vx_dfv_csr_read(vx_device_h device, uint32_t addr, uint32_t* value) {
  auto found = dfv_states.find(device);
  if (!value || found == dfv_states.end() || found->second.running
      || addr < VX_DFV_CSR_FIRST || addr >= VX_DFV_CSR_FIRST + VX_DFV_CSR_COUNT
      || found->second.config.applied != found->second.config.sequence) return -1;
  *value = found->second.config.readback[addr - VX_DFV_CSR_FIRST];
  return 0;
}

static int dfv_before_start(vx_device_h device) {
  auto found = dfv_states.find(device);
  if (found == dfv_states.end() || found->second.running) return -1;
  auto& state = found->second;
  state.config.applied = 0;
  return g_callbacks.copy_to_dev(state.buffer, &state.config, 0, sizeof(state.config));
}

static int dfv_after_wait(vx_device_h device) {
  auto& state = dfv_states.at(device);
  state.running = false;
  vx_dfv_config_t actual;
  if (g_callbacks.copy_from_dev(&actual, state.buffer, 0, sizeof(actual))) return -1;
  if (actual.magic != VX_DFV_CONFIG_MAGIC || actual.version != VX_DFV_CONFIG_VERSION
      || actual.applied != state.config.sequence) {
    if (!state.required) return 0;
    std::fprintf(stderr, "DFV_CSR_ERROR kernel did not apply this launch's host configuration\n");
    return -1;
  }
  for (unsigned i = 0; i < VX_DFV_CSR_COUNT; ++i) {
    if (actual.readback[i] != state.config.values[i]) {
      std::fprintf(stderr, "DFV_CSR_ERROR applied CSR 0x%x differs from requested value\n", VX_DFV_CSR_FIRST + i);
      return -1;
    }
    std::printf("DFV_HOST_CSR core=0 sequence=%u addr=0x%03x value=0x%08x\n",
                actual.applied, VX_DFV_CSR_FIRST + i, actual.readback[i]);
  }
  state.config = actual;
  return 0;
}
