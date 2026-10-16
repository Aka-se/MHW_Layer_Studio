#include "../random_damage.hpp"
#include <cstdio>
#include <limits>
#include <set>

int main()
{
    int failures = 0, checks = 0;
    auto check = [&](const char* name, bool pass) {
        ++checks; if (!pass) ++failures;
        std::printf("[%s] %s\n", pass ? "PASS" : "FAIL", name);
    };
    vce::RandomDamage pool(20, 37);
    pool.Update(95.01f); check("no hide before first 5 percent", pool.Count() == 0);
    pool.Update(95); check("one unit at first 5 percent", pool.Count() == 1);
    pool.Update(80); check("four hidden at eighty", pool.Count() == 4);
    std::set<unsigned> original;
    for (unsigned i=0; i<20; ++i) if (pool.Hidden(i)) original.insert(i);
    pool.Update(79); bool stable = true;
    for (unsigned i=0; i<20; ++i) stable &= pool.Hidden(i) == (original.count(i) != 0);
    check("within-stage polling never rerolls", stable);
    pool.Update(90); check("healing restores only two", pool.Count() == 2);
    bool subset = true;
    for (unsigned i=0; i<20; ++i) if (pool.Hidden(i)) subset &= original.count(i) != 0;
    check("healing preserves remaining hidden units", subset);
    std::set<unsigned> remain;
    for (unsigned i=0; i<20; ++i) if (pool.Hidden(i)) remain.insert(i);
    pool.Update(80); subset = true;
    for (unsigned i : remain) subset &= pool.Hidden(i);
    check("redamage keeps hidden remainder", subset && pool.Count() == 4);
    std::set<std::set<unsigned>> outcomes;
    for (unsigned run=0; run<10; ++run) {
        pool.Update(100); check("complete heal shows everything", pool.Count() == 0);
        pool.Update(80);
        std::set<unsigned> hidden;
        for (unsigned i=0; i<20; ++i) if (pool.Hidden(i)) hidden.insert(i);
        outcomes.insert(hidden);
    }
    check("new damage after healing can select different units", outcomes.size() > 1);
    pool.Update(0); check("zero percent hides all eligible units", pool.Count() == 20);
    pool.Update(std::numeric_limits<float>::quiet_NaN()); check("invalid HP resets", pool.Count() == 0);
    vce::RandomDamage small(3, 2);
    small.Update(95); check("small pools use proportional integer budgets", small.Count() == 0);
    small.Update(65); check("small pools eventually hide a unit", small.Count() == 1);
    small.Reset(); check("scene reset clears budget", small.Count() == 0);
    vce::RandomDamage empty;
    empty.Update(0); check("empty pool is safe", empty.Count() == 0);
    std::printf("%d checks, %d failures\n", checks, failures);
    return failures ? 1 : 0;
}
