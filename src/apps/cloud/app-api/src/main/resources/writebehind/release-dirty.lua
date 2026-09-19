if redis.call('HLEN', KEYS[1]) == 0 then
    return redis.call('SREM', KEYS[2], ARGV[1])
end
return 0
