local added = 0
for i = 1, #ARGV, 2 do
    if redis.call('HSETNX', KEYS[1], ARGV[i], ARGV[i + 1]) == 1 then
        redis.call('HINCRBY', KEYS[2], ARGV[i + 1], 1)
        added = added + 1
    end
end
return added
