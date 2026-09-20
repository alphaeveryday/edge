local choices = {'BUY', 'HOLD', 'SELL'}
local deltas = {}
for i, choice in ipairs(choices) do
    deltas[i] = tonumber(ARGV[i]) - tonumber(redis.call('HGET', KEYS[2], choice) or '0')
end
redis.call('DEL', KEYS[1], KEYS[2])
redis.call('HSET', KEYS[2], 'BUY', ARGV[1], 'HOLD', ARGV[2], 'SELL', ARGV[3])
for i = 4, #ARGV, 2 do
    redis.call('HSET', KEYS[1], ARGV[i], ARGV[i + 1])
end
return deltas
