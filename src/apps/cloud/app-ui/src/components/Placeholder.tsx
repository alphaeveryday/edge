import { Link, type Href } from 'expo-router';
import { StyleSheet, Text, View } from 'react-native';
import { colors, size, space } from '@/theme/tokens';

interface Props {
  title: string;
  links?: { label: string; href: Href }[];
}

export function Placeholder({ title, links = [] }: Props) {
  return (
    <View style={styles.root}>
      <Text style={styles.title}>{title}</Text>
      {links.map((l) => (
        <Link key={l.label} href={l.href} style={styles.link}>
          {l.label}
        </Link>
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.bg, padding: space.xl, gap: space.md },
  title: { fontSize: size.h3, fontWeight: '800', color: colors.text },
  link: { fontSize: size.base, color: colors.primary, paddingVertical: space.xs },
});
