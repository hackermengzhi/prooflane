-- Run in Cheat Engine's Lua console against your explicitly selected test process.
local pid = getOpenedProcessID()
assert(pid ~= 0, 'Select your authorized test process first')
print('Selected PID: ' .. pid)
print('Lua adapter loaded; no memory writes performed')
